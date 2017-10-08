import cv2
import numpy as np
import rasterio
import os
import pandas as pd
import glob
import image_registration
import numpy as np
from shapely import geometry
from rasterio import features
from affine import Affine
from rasterio import warp
import shapely
import json
import tqdm

def main(src_raster, ref_raster, grid_spacing_px, window_size, out_dir, n_iter=5000, term_eps=1e-10):

    gcps = []
    tiepoints = []
    with rasterio.open(src_raster, 'r') as src, rasterio.open(ref_raster, 'r') as ref:

        # extract src raster extent to boundary
        bounds = src.bounds
        bounds_geom = geometry.box(*bounds)

        pixel_size_x = src.profile['transform'][0]
        pixel_size_y = src.profile['transform'][4] * -1

        count_x = int(np.floor((bounds.right - bounds.left)/(grid_spacing_px * pixel_size_x)))
        pts_x = np.linspace(bounds.left + pixel_size_x * window_size/2., bounds.right, count_x)

        count_y = int(np.floor((bounds.top - bounds.bottom)/(grid_spacing_px * pixel_size_y)))
        pts_y = np.linspace(bounds.bottom + pixel_size_y * window_size/2., bounds.top, count_y)


        pts = []
        for x in pts_x:
            for y in pts_y:
                pts.append((x, y))


        for x, y in tqdm.tqdm(pts):

            src_pt = geometry.Point(x, y)

            # verify that hte point is within the bounds
            in_bounds = src_pt.intersects(bounds_geom)

            if in_bounds is False:
                # skip to the next point
                continue

            # find the pixel that this pt corresponds to the in the src rast
            src_px = src.index(x, y)
            src_row, src_col = src_px

            # calculate the window around this point
            left_pad = int(np.floor(window_size / 2.))
            top_pad = int(np.floor(window_size / 2.))
            # and the ceiling fro the right and bottom padding
            right_pad = int(np.ceil(window_size / 2.))
            bottom_pad = int(np.ceil(window_size / 2.))

            # set the window
            window_col_start = np.maximum(src_col - left_pad, 0)
            window_col_stop = np.minimum(src_col + right_pad, src.shape[1]-1)
            window_row_start = np.maximum(src_row - bottom_pad, 0)
            window_row_stop = np.minimum(src_row + top_pad, src.shape[0]-1)
            window = ((window_row_start, window_row_stop), (window_col_start, window_col_stop))

            # get the window from the src raster using the window
            src_rgb = np.zeros((window_row_stop-window_row_start, window_col_stop-window_col_start, 3), dtype=src.profile['dtype'])
            src_rgb[:, :, 0] = src.read(1, window=window)
            src_rgb[:, :, 1] = src.read(2, window=window)
            src_rgb[:, :, 2] = src.read(3, window=window)

            # convert window to a geom
            ref_window_x_min = x - (src_col - window_col_start) * pixel_size_x
            ref_window_x_max = x + (window_col_stop - src_col) * pixel_size_x

            ref_window_y_min = y - (src_row - window_row_start) * pixel_size_y
            ref_window_y_max = y + (window_row_stop - src_row) * pixel_size_y

            ref_window_ul_xy = (ref_window_x_min, ref_window_y_max)
            ref_window_lr_xy = (ref_window_x_max, ref_window_y_min)

            ref_window_ul_px = ref.index(*ref_window_ul_xy)
            ref_window_lr_px = ref.index(*ref_window_lr_xy)

            ref_window_row_stop = np.minimum(ref_window_lr_px[0], ref.shape[0]-1)
            ref_window_row_start = np.maximum(ref_window_ul_px[0], 0)
            ref_window_col_start = np.maximum(ref_window_ul_px[1], 0)
            ref_window_col_stop = np.minimum(ref_window_lr_px[1], ref.shape[1]-1)

            ref_window = ((ref_window_row_start, ref_window_row_stop), (ref_window_col_start, ref_window_col_stop))

            ref_r_raw = ref.read(1, window=ref_window)
            ref_g_raw = ref.read(2, window=ref_window)
            ref_b_raw = ref.read(3, window=ref_window)

            # create empty array that will hold the resampled reference raster data
            ref_rgb = np.zeros(src_rgb.shape, dtype=ref.profile['dtype'])

            # # create an affine transform for the subset data (so we can upsample via reproject and int w poly)
            t = ref.transform
            src_t = src.transform
            c_plus = ref_window[1][0]
            f_plus = ref_window[0][0]
            src_affine = Affine(t.a, t.b, t.c + c_plus * t.a, t.d, t.e, t.f + f_plus * t.e)
            shifted_affine = Affine(src_t.a, t.b, t.c + c_plus * t.a, t.d, src_t.e,
                                    t.f + f_plus * t.e)

            # reproject/upsample
            rasterio.warp.reproject(ref_r_raw, ref_rgb[:, :, 0], src_transform=src_affine, dst_transform=shifted_affine,
                                    src_crs=ref.crs, dst_crs=ref.crs, resample=warp.Resampling.cubic)
            rasterio.warp.reproject(ref_g_raw, ref_rgb[:, :, 1], src_transform=src_affine, dst_transform=shifted_affine,
                                    src_crs=ref.crs, dst_crs=ref.crs, resample=warp.Resampling.cubic)
            rasterio.warp.reproject(ref_b_raw, ref_rgb[:, :, 2], src_transform=src_affine, dst_transform=shifted_affine,
                                    src_crs=ref.crs, dst_crs=ref.crs, resample=warp.Resampling.cubic)


            # calculate the affine shift
            warp_matrix = image_registration.calculate_warp_matrix(src_rgb, ref_rgb,
                                                                   warp_mode=cv2.MOTION_TRANSLATION,
                                                                   n_iter=n_iter,
                                                                   term_eps=term_eps)
            # if no solution was found, skip to the next point
            if warp_matrix is None:
                continue

            # otherwise, extract transformation parameters
            a = warp_matrix[0][0]
            b = warp_matrix[0][1]
            xoff = warp_matrix[0][2] * -1 * pixel_size_x
            d = warp_matrix[1][0]
            e = warp_matrix[1][1]
            yoff = warp_matrix[1][2] * pixel_size_y

            src_pt_shifted = geometry.Point(src_pt.x + xoff, src_pt.y + yoff)

            gcp = (src_col, src_row, src_pt_shifted.x, src_pt_shifted.y)
            gcps.append(gcp)

            # tiepoints
            tiepoint = geometry.LineString([src_pt, src_pt_shifted])
            tiepoints.append({"type": "Feature", "properties": {}, "geometry": tiepoint.__geo_interface__})

    out_gcps = os.path.join(out_dir, 'gcps.txt')
    with open(out_gcps, 'w') as o:
        for gcp in gcps:
            vals = ' '.join(map(str, gcp))
            o.write('-gcp {}\n'.format(vals))

    out_tiepoints = os.path.join(out_dir, 'tiepoints.geojson')
    tiepoints_geojson = {
                            "type"    : "FeatureCollection",
                            "crs"     : {"type": "name", "properties": {"name": str(src.crs.values()[0])}},
                            "features": tiepoints
                        }
    with open(out_tiepoints, 'w') as o:
        json.dump(tiepoints_geojson, o)


if __name__ == '__main__':

    # src_raster = '/Users/mgleason/Desktop/temp/_blm/blm_clip.tif'
    # ref_raster = '/Users/mgleason/Desktop/temp/50_cm/50cm_clip.tif'
    # out_dir = '/Users/mgleason/Desktop/temp/test'
    src_raster = '/Users/mgleason/Desktop/temp/i2i/30cm.tif'
    ref_raster = '/Users/mgleason/Desktop/temp/i2i/50cm.tif'
    out_dir = '/Users/mgleason/Desktop/temp/i2i/output_faster/'

    grid_spacing_px = 501
    window_size = 501

    # grid_spacing_px = 2501
    # window_size = 2501

    main(src_raster, ref_raster, grid_spacing_px, window_size, out_dir, n_iter=1000, term_eps=1e-4)



# TODO: add ability to include a geojson to filter the search area
# TODO: write code to use multiprocessing to solve this faster
# for docker install, to get opencv to work: sudo apt install libgl1-mesa-glx