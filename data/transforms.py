
import random
import numpy as np
import PIL
import torch
import torchvision.transforms as T
import torchvision.transforms.functional as F
from torchvision.transforms.functional import resize, to_pil_image  # type: ignore

from copy import deepcopy
from typing import Tuple

# import albumentations as A
# from albumentations.pytorch import ToTensorV2

# from .util.misc import interpolate

# ------------------ Image Augs ----------------
def flip_img(img):
    """Flip rgb images or masks.
    channels come last, e.g. (256,256,3).
    """
    img = np.fliplr(img)
    return img


# ------------------ Transforms ----------------
def _dict_to_tensors(dict_obj):  # helper
    """convert a dictionary with numeric values into a new dictionary with torch tensors"""
    new_dict = dict.fromkeys(dict_obj.keys())
    for key, value in dict_obj.items():
        if key == 'image': 
            new_dict[key] = value
        else:
            if value is None:
                new_dict[key] = torch.Tensor()
            elif isinstance(value, dict):
                new_dict[key] = _dict_to_tensors(value)
            elif isinstance(value, str):  # no changes for strings
                new_dict[key] = value
            elif isinstance(value, np.ndarray):
                new_dict[key] = torch.from_numpy(value)

                # TODO more stable way of converting the types (or detecting ints)
                if value.dtype not in [int, np.int64, bool]:
                    new_dict[key] = new_dict[key].float()  # cast all doubles and ofther stuff to floats
            else:
                new_dict[key] = torch.tensor(np.asarray(value)).float()  # just try directly, if nothing else works
    return new_dict


# Custom transforms -- to tensor
class SampleToTensor(object):
    """Convert ndarrays in sample to Tensors."""
    
    def __call__(self, sample):        
        return _dict_to_tensors(sample)


class FeatureStandartization():
    """Normalize features of provided sample with given stats"""
    def __init__(self, shift, scale):
        self.shift = torch.Tensor(shift)
        self.scale = torch.Tensor(scale)
    
    def __call__(self, sample):
        updated_sample = {}
        for key, value in sample.items():
            if key == 'features':
                updated_sample[key] = (sample[key] - self.shift) / self.scale
            else: 
                updated_sample[key] = sample[key]

        return updated_sample


class GTtandartization():
    """Normalize features of provided sample with given stats
        * Supports multimodal gt represented as dictionary
        * For dictionary gts, only those values are updated for which the stats are provided
    """
    def __init__(self, shift, scale):
        """If ground truth is a dictionary in itself, the provided values should also be dictionaries"""
        
        self.shift = _dict_to_tensors(shift) if isinstance(shift, dict) else torch.Tensor(shift)
        self.scale = _dict_to_tensors(scale) if isinstance(scale, dict) else torch.Tensor(scale)
    
    def __call__(self, sample):
        gt = sample['ground_truth']
        if isinstance(gt, dict):
            new_gt = dict.fromkeys(gt.keys())
            for key, value in gt.items():
                new_gt[key] = value
                if key in self.shift:
                    new_gt[key] = new_gt[key] - self.shift[key]
                if key in self.scale:
                    new_gt[key] = new_gt[key] / self.scale[key]
                
                if key == "aug_outlines" and "outlines" in self.shift:
                    new_gt[key] = new_gt[key] - self.shift["outlines"][:2]
                if key == "aug_outlines" and "outlines" in self.scale:
                     new_gt[key] = new_gt[key] / self.scale["outlines"][:2]
                if key == "aug_outlines":
                    new_gt[key] = new_gt[key].to(gt["outlines"].dtype)
                # if shift and scale are not set, the value is kept as it is
        else:
            new_gt = (gt - self.shift) / self.scale

        # gather sample
        updated_sample = {}
        for key, value in sample.items():
            updated_sample[key] = new_gt if key == 'ground_truth' else sample[key]

        return updated_sample


# ------------------ Transforms for image ----------------

def crop(image, region):
    cropped_image = F.crop(image, *region)
    return cropped_image

def resize(image, size, max_size=None):
    # size can be min_size (scalar) or (w, h) tuple
    def get_size_with_aspect_ratio(image_size, size, max_size=None):
        w, h = image_size
        if max_size is not None:
            min_original_size = float(min((w, h)))
            max_original_size = float(max((w, h)))
            if max_original_size / min_original_size * size > max_size:
                size = int(round(max_size * min_original_size / max_original_size))

        if (w <= h and w == size) or (h <= w and h == size):
            return (h, w)

        if w < h:
            ow = size
            oh = int(size * h / w)
        else:
            oh = size
            ow = int(size * w / h)

        return (oh, ow)
    def get_size(image_size, size, max_size=None):
        if isinstance(size, (list, tuple)):
            return size[::-1]
        else:
            return get_size_with_aspect_ratio(image_size, size, max_size)

    size = get_size(image.size, size, max_size)
    rescaled_image = F.resize(image, size)
    return rescaled_image

class RandomCrop(object):
    def __init__(self, size):
        self.size = size
    
    def __call__(self, img):
        region = T.RandomCrop.get_params(img, self.size)
        return crop(img, region)

class RandomSizeCrop(object):
    def __init__(self, min_size: int, max_size: int):
        self.min_size = min_size
        self.max_size = max_size

    def __call__(self, img: PIL.Image.Image):
        w = random.randint(self.min_size, min(img.width, self.max_size))
        h = random.randint(self.min_size, min(img.height, self.max_size))
        region = T.RandomCrop.get_params(img, [h, w])
        return crop(img, region)

class CenterCrop(object):
    def __init__(self, size):
        self.size = size

    def __call__(self, img):
        image_width, image_height = img.size
        crop_height, crop_width = self.size
        crop_top = int(round((image_height - crop_height) / 2.))
        crop_left = int(round((image_width - crop_width) / 2.))
        return crop(img, (crop_top, crop_left, crop_height, crop_width))

class Normalize(object):
    def __init__(self, mean, std):
        self.mean = mean
        self.std = std
    
    def __call__(self, img):
        image = F.normalize(image, mean=self.mean, std=self.std)
        return image 
    
class PCDNormalizeUnitCube(object):
    def __call__(self, pcd):
        p_max = pcd[..., :3].max(0)
        p_min = pcd[..., :3].min(0)
        pcd_center = (p_max + p_min) / 2.
        pcd_scale = (p_max - p_min).max() / 2.
        pcd[..., :3] = (pcd[..., :3] - pcd_center) / pcd_scale
        return pcd
    
class Compose(object):
    def __init__(self, transforms):
        self.transforms = transforms
    
    def __call__(self, image):
        for t in self.transforms:
            image = t(image)
        return image

    def __repr__(self):
        format_string = self.__class__.__name__ + "("
        for t in self.transforms:
            format_string += "\n"
            format_string += "    {0}".format(t)
        format_string += "\n)"
        return format_string


def tv_make_color_img_transforms():
    return T.Compose([
        T.GaussianBlur(kernel_size=(5, 9), sigma=(0.1, 5)),
        T.ColorJitter(brightness=.5, hue=.3),
    ])

def tv_make_geo_img_transforms(color=0):
    print(color)
    return T.RandomApply(transforms= [
        T.RandomPerspective(distortion_scale=0.2, p=0.8, fill=color),
        T.RandomRotation(degrees=(0, 45), fill=color),
        T.RandomAffine(degrees=(0, 0), translate=(0.2, 0.1), scale=(0.75, 1), fill=color),
        T.RandomPosterize(bits=2)
    ])
def tv_make_geo_transform():
    return T.Compose([
        PCDNormalizeUnitCube(),
    ])
    
def tv_make_img_transforms_clip():
    return T.Compose([
        T.ToTensor(),
        T.CenterCrop(800),
        T.Resize(224),
        T.Normalize(mean=[0.48145466, 0.4578275, 0.40821073], std=[0.26862954, 0.26130258, 0.27577711])
    ])
    
def tv_make_img_transforms():
    return T.Compose([
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        T.CenterCrop(400),
        T.Resize(384)
    ])
    

def denormalize_img_transforms(mean=torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32), 
                              std=torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32)):
    return T.Compose(
        [
     #       T.Normalize((-mean/std).tolist(), (1.0/std).tolist()),
            T.ToPILImage()
        ]
    )


# def make_image_transforms():
#     return A.Compose([
#         A.CenterCrop(800, 800),
#         A.Resize(384, 384),
#         A.Normalize(mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)),
#         ToTensorV2(),
#     ])

# def make_smpl_uv_transforms():
#     return A.Compose([
#         A.CenterCrop(800, 800),
#         A.Resize(384, 384),
#         A.Normalize(mean=(0, 0, 0), std=(1.0, 1.0, 1.0)),
#         ToTensorV2(),
#     ])




# def make_image_geo_augments():
#     return A.Compose([
#         A.Perspective(),
#         A.Affine(rotate=(0, 45), translate_percent=(0.2, 0.1), scale=(0.75, 1)),
#     ])

# def make_image_color_augments():
#     return A.Compose([
#         A.ColorJitter(brightness=.5, hue=.3)
#     ])


if __name__ == '__main__':
    from PIL import Image
    import requests
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    def plot(imgs, idx, with_orig=True, row_title=None, **imshow_kwargs):

        num_rows = 4
        num_cols = 4
        fig, axs = plt.subplots(nrows=num_rows, ncols=num_cols, squeeze=False)
        for i in range(4):
            for j in range(4):
                ax = axs[i, j]
                print(((imgs[i * 4 + j])/255.).max(), i * 4 + j)
                ax.imshow(np.asarray(imgs[i * 4 + j])/255., **imshow_kwargs)
                ax.set(xticklabels=[], yticklabels=[], xticks=[], yticks=[])

        plt.tight_layout()
        fig.savefig("test_aug_{}.png".format(idx))
        plt.close(fig)
        
        
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

# This source code is licensed under the license found in the
# LICENSE file in the root directory of this source tree.



class ResizeLongestSide:
    """
    Resizes images to the longest side 'target_length', as well as provides
    methods for resizing coordinates and boxes. Provides methods for
    transforming both numpy array and batched torch tensors.
    """

    def __init__(self, target_length: int) -> None:
        self.target_length = target_length

    def apply_image(self, image: np.ndarray) -> np.ndarray:
        """
        Expects a numpy array with shape HxWxC in uint8 format.
        """
        target_size = self.get_preprocess_shape(image.shape[0], image.shape[1], self.target_length)
        return np.array(resize(to_pil_image(image), target_size))

    def apply_coords(self, coords: np.ndarray, original_size: Tuple[int, ...]) -> np.ndarray:
        """
        Expects a numpy array of length 2 in the final dimension. Requires the
        original image size in (H, W) format.
        """
        old_h, old_w = original_size
        new_h, new_w = self.get_preprocess_shape(
            original_size[0], original_size[1], self.target_length
        )
        coords = deepcopy(coords).astype(float)
        coords[..., 0] = coords[..., 0] * (new_w / old_w)
        coords[..., 1] = coords[..., 1] * (new_h / old_h)
        return coords

    def apply_boxes(self, boxes: np.ndarray, original_size: Tuple[int, ...]) -> np.ndarray:
        """
        Expects a numpy array shape Bx4. Requires the original image size
        in (H, W) format.
        """
        boxes = self.apply_coords(boxes.reshape(-1, 2, 2), original_size)
        return boxes.reshape(-1, 4)

    def apply_image_torch(self, image: torch.Tensor) -> torch.Tensor:
        """
        Expects batched images with shape BxCxHxW and float format. This
        transformation may not exactly match apply_image. apply_image is
        the transformation expected by the model.
        """
        # Expects an image in BCHW format. May not exactly match apply_image.
        target_size = self.get_preprocess_shape(image.shape[2], image.shape[3], self.target_length)
        return F.interpolate(
            image, target_size, mode="bilinear", align_corners=False, antialias=True
        )

    def apply_coords_torch(
        self, coords: torch.Tensor, original_size: Tuple[int, ...]
    ) -> torch.Tensor:
        """
        Expects a torch tensor with length 2 in the last dimension. Requires the
        original image size in (H, W) format.
        """
        old_h, old_w = original_size
        new_h, new_w = self.get_preprocess_shape(
            original_size[0], original_size[1], self.target_length
        )
        coords = deepcopy(coords).to(torch.float)
        coords[..., 0] = coords[..., 0] * (new_w / old_w)
        coords[..., 1] = coords[..., 1] * (new_h / old_h)
        return coords

    def apply_boxes_torch(
        self, boxes: torch.Tensor, original_size: Tuple[int, ...]
    ) -> torch.Tensor:
        """
        Expects a torch tensor with shape Bx4. Requires the original image
        size in (H, W) format.
        """
        boxes = self.apply_coords_torch(boxes.reshape(-1, 2, 2), original_size)
        return boxes.reshape(-1, 4)

    @staticmethod
    def get_preprocess_shape(oldh: int, oldw: int, long_side_length: int) -> Tuple[int, int]:
        """
        Compute the output size given input size and target long side length.
        """
        scale = long_side_length * 1.0 / max(oldh, oldw)
        newh, neww = oldh * scale, oldw * scale
        neww = int(neww + 0.5)
        newh = int(newh + 0.5)
        return (newh, neww)