import tiepoints2gcps
import os
import json
import glob


def convert_type(var, f, expected_type):

    # try to convert the inputs to correct types
    try:
        var = f(var)
    except ValueError, e:
        err = "Inputs {var} cannot be converted to type {expected_type}".format(var=var,
                                                                                expected_type=expected_type)
        raise ValueError(err)

    return var


def main():

    # get the inputs
    input_folder_src = '/mnt/work/input/source'
    input_folder_ref = '/mnt/work/input/reference'
    input_folder_aoi = '/mnt/work/input/aoi_geojson'
    string_ports = '/mnt/work/input/ports.json'

    # create output directory
    out_path = '/mnt/work/output/data'
    if os.path.exists(out_path) is False:
        os.makedirs(out_path)

    # read the inputs
    with open(string_ports) as ports:
        inputs = json.load(ports)
    grid_spacing_px = inputs.get('grid_spacing_px', '501')
    window_size_px = inputs.get('window_size_px', '501')
    n_iter = inputs.get('n_iter', '1000')
    term_eps = inputs.get('term_eps', '1e-4')
    src_nodata = inputs.get('src_nodata', '0')
    ref_nodata = inputs.get('ref_nodata', '0')

    # convert the inputs to the correct dtypes
    grid_spacing_px = convert_type(grid_spacing_px, int, 'Integer')
    window_size_px = convert_type(window_size_px, int, 'Integer')
    n_iter = convert_type(n_iter, int, 'Integer')
    term_eps = convert_type(term_eps, float, 'Float')
    src_nodata = convert_type(src_nodata, float, 'Float')
    ref_nodata = convert_type(ref_nodata, float, 'Float')

    # get the input aoi geojson (if provided)
    if os.path.exists(input_folder_aoi):
        aoi_geojsons = glob.glob1(input_folder_aoi, '*.json') + glob.glob1(input_folder_aoi, '*.geojson')
        if len(aoi_geojsons) == 0:
            raise ValueError("No GeoJSONs found in input data port 'aoi_geojsons'")
        if len(aoi_geojsons) > 1:
            raise ValueError("Multiple GeoJSONs found in input data port 'aoi_geojsons'")
        aoi_geojson = os.path.join(input_folder_aoi, aoi_geojsons[0])
    else:
        aoi_geojson = None

    # get the rasters in the reference folder
    ref_rasters = glob.glob1(input_folder_ref, '*.tif')
    if len(ref_rasters) == 0:
        raise ValueError("No tifs found in input data port 'reference'")
    if len(ref_rasters) > 1:
        raise ValueError("Multiple shapefiles found in input data port 'reference'")
    ref_raster = os.path.join(input_folder_ref, ref_rasters[0])

    # get the rasters in the source folder
    src_rasters = glob.glob1(input_folder_src, '*.tif')
    if len(src_rasters) == 0:
        raise ValueError("No tifs found in input data port 'source'")
    if len(src_rasters) > 1:
        raise ValueError("Multiple shapefiles found in input data port 'source'")
    src_raster = os.path.join(input_folder_src, src_rasters[0])

    # run the processing
    tiepoints2gcps.main(src_raster, ref_raster, grid_spacing_px, window_size_px, out_path, src_nodata, ref_nodata,
                        aoi_geojson, n_iter, term_eps)

    print "Tiepoint creation completed successfully."


if __name__ == '__main__':

    main()