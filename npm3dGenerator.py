import os
import numpy as np
import matplotlib.pyplot as plt
from tqdm import tqdm_notebook
from keras.utils import to_categorical
from sklearn.neighbors import KDTree
from plyfile import PlyData, PlyElement
from ply import write_ply
import keras

def get_cov(points):
    points -= np.mean(points, axis = 0)
    
    return points.transpose().dot(points) / points.shape[0]

def compute_normals(cloud, tree, radius = .25):
    neighborhoods = tree.query_radius(cloud, r=radius)
    
    cov = [get_cov(cloud[neighborhood]) for neighborhood in neighborhoods]
    
    eigen_values, eigen_vectors = np.linalg.eigh(cov)
    mini_eigen_values = np.argmin(eigen_values, axis = -1)

    normals = np.array([vectors[:, mini] for vectors, mini in zip(eigen_vectors, mini_eigen_values)])
    normals = (normals.transpose() / np.sum(normals**2, axis = -1)**.5).transpose()
    return normals

def random_rotate_z(cloud, normal = None):
    rotation = 2 * np.pi * np.random.random()
    rotation_matrix = np.array([[np.cos(rotation), -np.sin(rotation), 0],
                                [np.sin(rotation), np.cos(rotation), 0],
                                [0, 0, 1.]])
    
    if(normal is None):return cloud.dot(rotation_matrix), None
    else:return cloud.dot(rotation_matrix), normal.dot(rotation_matrix)

def preprocess_cloud(cloud, normal = None):
    cloud -= np.mean(cloud, axis = 0)
    cloud, normal = random_rotate_z(cloud, normal)
    if(normal is None):return cloud
    else:return np.concatenate([cloud, normal], axis = -1)

def from_categorical(label):
    return label.dot(1 + np.arange(label.shape[-1]))

class NPM3DGenerator(keras.utils.Sequence):
    'Generates data for Keras'
    def __init__(self, n_points = 4096, batch_size = 8, input_dir = "../Benchmark_MVA/training", train = True, paths_to_keep = None, use_normals = True, compute_normals = False, normal_radius = .25):
        'Initialization'
        
        self.class_dict = {0 : "unclassified", 1 : "ground", 2 : "buildings", 3 : "poles", 4 : "pedestrians", 5 : "cars", 6 : "vegetation"}
        self.n_classes = len(self.class_dict) - 1
        
        self.input_dir = input_dir
        self.paths_to_keep = paths_to_keep
        
        self.batch_size = batch_size
        self.n_points = n_points
        self.train = train
        self.normal_radius = normal_radius
        self.use_normals = use_normals
        self.compute_normals = compute_normals
        self.use_precomputed_normals = use_normals and not compute_normals
        self.n_channels = 3
        if(self.use_normals):self.n_channels += 3
        
        self.prepare_NPM3D()
        
    def load_point_cloud(self, input_dir):
        data = PlyData.read(input_dir)
        columns = ["x", "y", "z"]
        if(self.use_precomputed_normals):columns += ["nx", "ny", "nz", "scalar_class"]
        else:columns += ["class"]
        data = np.array([data.elements[0].data[i] for i in columns[:len(data.elements[0].properties)]]).transpose()
        cloud = data[:, :3]
        tree = KDTree(cloud)
        normal = None
        if(self.use_normals):
            if(self.use_precomputed_normals):
                normal = data[:, 3:6]
            else:
                normal = compute_normals(cloud, tree, self.normal_radius)
        label = to_categorical(data[:, -1], num_classes = self.n_classes + 1)[:, 1:] if self.train else None
        return cloud, tree, normal, label
    
    def compute_class_weight(self):
        sum_labels = np.mean(np.concatenate(self.labels), axis = 0)
        sum_labels = np.clip(sum_labels, .0001, 1.)
        self.class_weight = 1. / sum_labels
    
    def prepare_NPM3D(self):
        self.paths = os.listdir(self.input_dir)
        if(self.use_precomputed_normals):self.paths = [path for path in self.paths if path.split(".")[0][-8:] == "_normals"]
        else:self.paths = [path for path in self.paths if path.split(".")[0][-8:] != "_normals"]
        if(not self.paths_to_keep is None):
            self.paths = [path for i, path in enumerate(self.paths) if i in self.paths_to_keep]
        
        self.clouds = []
        self.trees = []
        if(self.use_normals):self.normals = []
        if(self.train):self.labels = []
        
        for path in tqdm_notebook(self.paths):
            cloud, tree, normal, label = self.load_point_cloud(os.path.join(self.input_dir, path))
            self.clouds.append(cloud)
            self.trees.append(tree)
            if(self.use_normals):self.normals.append(normal)
            if(self.train):self.labels.append(label)
        
        self.n_points_clouds = [len(c) for c in self.clouds]
        
        self.n_points_total = np.sum(self.n_points_clouds)
        self.n_clouds = len(self.clouds)
        
        if(self.train):self.compute_class_weight()
    
    def __len__(self):
        'Denotes the number of batches per epoch'
        return int(np.floor(self.n_points_total / (self.batch_size * self.n_points)))

    def __getitem__(self, index):
        'Generate one batch of data'
        # Generate indexes of the batch
        indexes = np.random.choice(self.n_clouds, self.batch_size)

        # Generate data
        X, y = self.__data_generation(indexes)

        return X, y
    
    def sample_point_cloud(self, original_point_cloud = 0, center_point = None):
        
        if(center_point is None):center_point = np.random.randint(self.n_points_clouds[original_point_cloud])
        dist, ind = self.trees[original_point_cloud].query([self.clouds[original_point_cloud][center_point]], k = self.n_points)
        
        if(self.use_normals):
            cloud = preprocess_cloud(self.clouds[original_point_cloud][ind[0]], self.normals[original_point_cloud][ind[0]])
        else:
            cloud = preprocess_cloud(self.clouds[original_point_cloud][ind[0]])
        
        if(self.train):
            label = self.labels[original_point_cloud][ind[0]]
            return cloud, label
        else:
            return cloud, ind[0], dist[0]      
            
    def __data_generation(self, indexes):
        'Generates data containing batch_size samples'
        # Initialization
        # Generate data
        
        clouds = np.empty((self.batch_size, self.n_points, self.n_channels))
        labels = np.empty((self.batch_size, self.n_points, self.n_classes))
        
        for i, index in enumerate(indexes):
            cloud, label = self.sample_point_cloud(index)
            clouds[i] = cloud.copy()
            labels[i] = label.copy()

        return clouds, labels
    
    def predict_point_cloud(self, model, index = 0, epsilon_weights = .1, output_path = None):
        all_indexes = np.arange(self.n_points_clouds[index])
        predictions = np.zeros((self.n_points_clouds[index], self.n_classes))
        weights = np.zeros(self.n_points_clouds[index])
        
        pbar, pbar_value, pbar_update = tqdm_notebook(total=100), 0, 0
        while np.min(weights) < epsilon_weights:
            center_points = all_indexes[weights < epsilon_weights]
            cloud, ind, dist = self.sample_point_cloud(original_point_cloud = index, center_point = center_points[np.random.randint(len(center_points))])
            weight = 1. / np.clip(dist, .1 * np.max(dist), 10.)
            
            prediction = model.predict(np.expand_dims(cloud, axis = 0))[0]
            predictions[ind] += (prediction.transpose() * weight).transpose() 
            weights[ind] += weight
            
            int_pbar_value = int(pbar_value)
            pbar_value = 100 * np.mean(weights > epsilon_weights)
            pbar_update = int(pbar_value) - int_pbar_value
            for i in range(pbar_update):
                pbar.update()
        pbar.close()
        
        predictions = (predictions.transpose() * weights).transpose()
        
        if(output_path is None):output_path = self.paths[index].split(".")[0] + "_prediction.ply"
        
        if(self.use_normals):
            write_ply(output_path,
                      [self.clouds[index], self.normals[index], np.argmax(predictions, axis = -1).astype(int)],
                      ['x', 'y', 'z', 'nx', 'ny', 'nz', 'class'])
        else:
            write_ply(output_path,
                      [self.clouds[index], np.argmax(predictions, axis = -1)],
                      ['x', 'y', 'z', 'class'])
        
        return predictions