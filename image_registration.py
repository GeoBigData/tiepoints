import cv2
import numpy as np
import rasterio
import os
import pandas as pd


def calculate_warp_matrix(source, reference, warp_mode=cv2.MOTION_TRANSLATION, n_iter=5000, term_eps=1e-10):

    # warp_mode = cv2.MOTION_TRANSLATION
    # warp_mode = cv2.MOTION_HOMOGRAPHY

    # Convert images to grayscale
    ref_gray = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    src_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)

    # Find size of image1
    sz = reference.shape

    # Define the motion model

    # Define 2x3 or 3x3 matrices and initialize the matrix to identity
    if warp_mode == cv2.MOTION_HOMOGRAPHY:
        warp_matrix = np.eye(3, 3, dtype=np.float32)
    else:
        warp_matrix = np.eye(2, 3, dtype=np.float32)

    # Define termination criteria
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, n_iter, term_eps)

    # Run the ECC algorithm. The results are stored in warp_matrix.
    try:
        (cc, warp_matrix) = cv2.findTransformECC(ref_gray, src_gray, warp_matrix, warp_mode, criteria)
    except Exception, e:
        return None

    return warp_matrix


def apply_warp_matrix(source, warp_matrix, warp_mode=cv2.MOTION_TRANSLATION):

    sz = source.shape

    if warp_mode == cv2.MOTION_HOMOGRAPHY:
        # Use warpPerspective for Homography
        src_realigned = cv2.warpPerspective(source, warp_matrix, (sz[1], sz[0]), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP)
    else:
        # Use warpAffine for Translation, Euclidean and Affine
        src_realigned = cv2.warpAffine(source, warp_matrix, (sz[1], sz[0]), flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP) ;

    return src_realigned


def output_geotiff(source, src_realigned, outpath):

    with rasterio.open(source, 'r') as src:
        out_profile = src.profile

        with rasterio.open(outpath, 'w', **out_profile) as dst:
            dst.write(src_realigned[:,:,0], 1)
            dst.write(src_realigned[:,:,1], 2)
            dst.write(src_realigned[:,:,2], 3)




