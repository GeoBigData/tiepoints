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
import fiona


def calculate_tiepoint(src_pt, src, ref, window_size, src_nodata, ref_nodata, n_iter, term_eps):

    x = src_pt.x
    y = src_pt.y

    pixel_size_x = src.profile['transform'][0]
    pixel_size_y = src.profile['transform'][4] * -1

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
    window_col_start = src_col - left_pad
    window_col_stop = src_col + right_pad
    window_row_start = src_row - bottom_pad
    window_row_stop = src_row + top_pad
    window = ((window_row_start, window_row_stop), (window_col_start, window_col_stop))

    # check that the window is totally within the image bounds. if not, skip it
    if window_col_start < 0 or window_col_stop > src.shape[1]-1 \
            or window_row_start < 0 or window_row_stop > src.shape[0] - 1:
        return None, None

    # get the window from the src raster using the window
    src_rgb = np.zeros((window_row_stop - window_row_start, window_col_stop - window_col_start, 3),
                       dtype=src.profile['dtype'])
    src_rgb[:, :, 0] = src.read(1, window=window)
    src_rgb[:, :, 1] = src.read(2, window=window)
    src_rgb[:, :, 2] = src.read(3, window=window)

    # check for nodata -- if any, skip
    # this avoids calculations of tie points along boundary images, where errors can occur
    src_band_sum = src_rgb.sum(axis=2)
    # pct no_data
    src_pct_nodata = np.sum(src_band_sum == src_nodata, dtype='float64')/src_band_sum.size
    if src_pct_nodata > 0.05:
        return None, None

    # convert window to a geom
    ref_window_x_min = x - (src_col - window_col_start) * pixel_size_x
    ref_window_x_max = x + (window_col_stop - src_col) * pixel_size_x

    ref_window_y_min = y - (src_row - window_row_start) * pixel_size_y
    ref_window_y_max = y + (window_row_stop - src_row) * pixel_size_y

    ref_window_ul_xy = (ref_window_x_min, ref_window_y_max)
    ref_window_lr_xy = (ref_window_x_max, ref_window_y_min)

    ref_window_ul_px = ref.index(*ref_window_ul_xy)
    ref_window_lr_px = ref.index(*ref_window_lr_xy)

    ref_window_row_stop = ref_window_lr_px[0]
    ref_window_row_start = ref_window_ul_px[0]
    ref_window_col_start = ref_window_ul_px[1]
    ref_window_col_stop = ref_window_lr_px[1]

    ref_window = ((ref_window_row_start, ref_window_row_stop), (ref_window_col_start, ref_window_col_stop))

    # check that the window is totally within the image bounds. if not, skip it
    if ref_window_col_start < 0 or ref_window_col_stop > ref.shape[1] - 1 \
            or ref_window_row_start < 0 or ref_window_row_stop > ref.shape[0] - 1:
        return None, None

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

    # check for nodata -- if any, skip
    # this avoids calculations of tie points along boundary images, where errors can occur
    ref_band_sum = ref_rgb.sum(axis=2)
    # pct no_data
    ref_pct_nodata = np.sum(ref_band_sum == ref_nodata, dtype='float64')/ref_band_sum.size
    if ref_pct_nodata > 0.05:
        return None, None

    # calculate the affine shift
    warp_matrix = image_registration.calculate_warp_matrix(src_rgb, ref_rgb,
                                                           warp_mode=cv2.MOTION_TRANSLATION,
                                                           n_iter=n_iter,
                                                           term_eps=term_eps)
    # if no solution was found, skip to the next point
    if warp_matrix is None:
        # return nothing
        return None, None

    # otherwise, extract transformation parameters
    a = warp_matrix[0][0]
    b = warp_matrix[0][1]
    xoff = warp_matrix[0][2] * -1 * pixel_size_x
    d = warp_matrix[1][0]
    e = warp_matrix[1][1]
    yoff = warp_matrix[1][2] * pixel_size_y

    src_pt_shifted = geometry.Point(src_pt.x + xoff, src_pt.y + yoff)

    gcp = (src_col, src_row, src_pt_shifted.x, src_pt_shifted.y)

    # tiepoints
    tiepoint = geometry.LineString([src_pt, src_pt_shifted])
    tiepoint_geojson = {"type": "Feature", "properties": {}, "geometry": tiepoint.__geo_interface__}

    return gcp, tiepoint_geojson


def main(src_raster, ref_raster, grid_spacing_px, window_size, out_dir, src_nodata, ref_nodata, aoi_geojson,
         n_iter=5000, term_eps=1e-10):

    with rasterio.open(src_raster, 'r') as src, rasterio.open(ref_raster, 'r') as ref:
        

        # check that the CRSs of the two rasters matche
        if src.crs <> ref.crs:
            raise ValueError('Source and reference raster do not have matching Coordinate Reference Systems.')

        # BUILD UP LIST OF GRID POINTS IN REFERENCE RASTER TO SEARCH FOR TIEPOINTS
        # extract src raster extent to boundary
        bounds = src.bounds
        # convert to a geometry (need this later)
        bounds_geom = geometry.box(*bounds)

        # get pixel sizes
        pixel_size_x = src.profile['transform'][0]
        pixel_size_y = src.profile['transform'][4] * -1

        # build up the x coordinates
        count_x = int(np.floor((bounds.right - bounds.left)/(grid_spacing_px * pixel_size_x)))
        pts_x = np.linspace(bounds.left + pixel_size_x * window_size/2., bounds.right, count_x)

        # build up the y coordinates
        count_y = int(np.floor((bounds.top - bounds.bottom)/(grid_spacing_px * pixel_size_y)))
        pts_y = np.linspace(bounds.bottom + pixel_size_y * window_size/2., bounds.top, count_y)

        # combine all combos of x,y into pt geometries
        src_pts = []
        for x in pts_x:
            for y in pts_y:
                src_pt = geometry.Point(x, y)
                src_pts.append(src_pt)

        # if aoi_geojson was provided, build up a list of the component polygon geometries
        aoi_geoms = []
        if aoi_geojson is not None:
            with fiona.open(aoi_geojson, 'r') as aois:

                # check the crs matches the ref raster
                if aois.crs <> ref.crs:
                    err = 'AOIs GeoJSON and reference raster do not have matching Coordinate Reference Systems.'
                    raise ValueError(err)

                # get and convert the geometries
                for feat in aois:
                    geom_wkt = feat['geometry']
                    if geom_wkt['type'] not in ['Polygon', 'MultiPolygon']:
                        err = "Input aoi_geojson file contains geometries that are not Polygon or MultiPolygon."
                        raise ValueError()
                    feat_geom = geometry.shape(geom_wkt)
                    aoi_geoms.append(feat_geom)


        # iterate over the pt geometries, searching for tie points
        gcps = []
        tiepoints = []
        for src_pt in tqdm.tqdm(src_pts):
            # verify that hte point is within the source raster bounds
            in_bounds = src_pt.intersects(bounds_geom)

            if in_bounds is False:
                # skip to the next point
                continue

            # verify that the point is within the aoi_geoms bounds
            if len(aoi_geoms) == 0:
                in_aoi_bounds = True
            else:
                in_aoi_bounds = False
                for aoi_geom in aoi_geoms:
                    in_aoi_bounds = in_aoi_bounds or src_pt.intersects(aoi_geom)
            # if not, skip to the next point
            if in_aoi_bounds is False:
                continue

            # find tiepoint (if possible) in reference raster. return result as a gcp and as a line geometry showing
            # the corresponding pts in the source nad reference rasters
            gcp, tiepoint = calculate_tiepoint(src_pt, src, ref, window_size, src_nodata, ref_nodata, n_iter, term_eps)
            tiepoints.append(tiepoint)
            gcps.append(gcp)

    out_gcps = os.path.join(out_dir, 'gcps.txt')
    with open(out_gcps, 'w') as o:
        for gcp in gcps:
            if gcp is not None:
                vals = ' '.join(map(str, gcp))
                o.write('-gcp {}\n'.format(vals))

    out_tiepoints = os.path.join(out_dir, 'tiepoints.geojson')
    tiepoints_geojson = {
                            "type"    : "FeatureCollection",
                            "crs"     : {"type": "name", "properties": {"name": str(src.crs.values()[0])}},
                            "features": [tp for tp in tiepoints if tp is not None]
                        }
    with open(out_tiepoints, 'w') as o:
        json.dump(tiepoints_geojson, o)


if __name__ == '__main__':

    src_raster = '/Users/mgleason/Desktop/temp/_blm/blm_clip.tif'
    ref_raster = '/Users/mgleason/Desktop/temp/50_cm/50cm_clip.tif'
    out_dir = '/Users/mgleason/Desktop/temp/test4'
    aoi_geojson = '/Users/mgleason/Desktop/temp/50_cm/boundary_test.geojson'
    # src_raster = '/Users/mgleason/Desktop/temp/i2i/30cm.tif'
    # ref_raster = '/Users/mgleason/Desktop/temp/i2i/50cm.tif'
    # out_dir = '/Users/mgleason/Desktop/temp/i2i/output_faster/'


    # these seem to be good default values
    src_nodata = 0
    ref_nodata = 0
    grid_spacing_px = 501
    window_size = 501
    n_iter=1000
    term_eps=1e-4

    # # use this for testing for now
    # grid_spacing_px = 251
    # window_size = 251
    # n_iter = 5000
    # term_eps=1e-10
    main(src_raster, ref_raster, grid_spacing_px, window_size, out_dir, src_nodata, ref_nodata, aoi_geojson,
         n_iter=n_iter, term_eps=term_eps)


# TODO: add ability to include a geojson to filter the search area
# TODO: write code to use multiprocessing to solve this faster -- too hard, couldn't figure it out
# for docker install, to get opencv to work: sudo apt install libgl1-mesa-glx