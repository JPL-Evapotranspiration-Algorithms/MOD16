'''
'''

import json
import os
import yaml
import numpy as np
import h5py
import mod16
from tqdm import tqdm
from mod16 import MOD16
from mod16.utils import restore_bplut, pft_dominant
from mod17.science import nash_sutcliffe
from SALib.sample.sobol import sample as sobol_sample
from SALib.analyze import sobol

OUTPUT_TPL = '/home/arthur/Workspace/NTSG/projects/Y2021_MODIS-VIIRS/data/MOD16_sensitivity_%s_analysis_rbl-switch.json'
MOD16_DIR = os.path.dirname(mod16.__file__)
with open(os.path.join(MOD16_DIR, 'data/MOD16_calibration_config.yaml'), 'r') as file:
    CONFIG = yaml.safe_load(file)
BOUNDS = {
    "tmin_close": [-35, 0],
    "tmin_open": [0, 25],
    "vpd_open": [0, 1000],
    "vpd_close": [1000, 8000],
    "gl_sh": [0.001, 0.2],
    "gl_wv": [0.001, 0.2],
    "g_cuticular": [1e-7, 1e-2],
    "csl": [0.0001, 0.1],
    "rbl_min": [10, 1000],
    "rbl_max": [100, 2000],
    "beta": [0, 2000]
}


def main(pft = None):
    # Stratify the data using the validation mask so that an equal number of
    #   samples from each PFT are used
    drivers, tower_obs = load_data(pft = pft, validation_mask_only = pft is None)
    params = MOD16.required_parameters
    problem = {
        'num_vars': len(params),
        'names': params,
        'bounds': [
            BOUNDS[p]
            for p in params
        ]
    }
    # NOTE: Number of samples must be a power of 2
    param_sweep = sobol_sample(problem, 256 if pft is None else 128)
    Y = np.zeros([param_sweep.shape[0]])
    for i, X in enumerate(tqdm(param_sweep)):
        yhat = MOD16._et(X, *drivers)
        Y[i] = nash_sutcliffe(yhat, tower_obs, norm = True)
    metrics = sobol.analyze(problem, Y)
    filename = OUTPUT_TPL % 'ET'
    if pft is not None:
        filename = OUTPUT_TPL % f'ET-PFT{pft}'
    with open(filename, 'w') as file:
        json.dump(dict([(k, v.tolist()) for k, v in metrics.items()]), file)


def load_data(pft, validation_mask_only = False):
    print('Loading driver datasets...')
    met_group = CONFIG['data']['met_group']
    with h5py.File(CONFIG['data']['file'], 'r') as hdf:
        nsteps = hdf['time'].shape[0]
        if pft is not None:
            site_list = hdf['FLUXNET/site_id'][:].tolist()
            if hasattr(site_list[0], 'decode'):
                site_list = [s.decode('utf-8') for s in site_list]
            sites = pft_dominant(hdf['state/PFT'][:], site_list = site_list)
            sites = sites == pft
        else:
            shp = hdf[f'{met_group}/Tmin'].shape
            sites = np.ones(shp[1]).astype(bool)
        lw_net_day = hdf[f'{met_group}/LWGNT_daytime'][:][:,sites]
        lw_net_night = hdf[f'{met_group}/LWGNT_nighttime'][:][:,sites]
        sw_albedo = np.nanmean(
            hdf[CONFIG['data']['datasets']['albedo']][:][:,sites], axis = -1)
        sw_rad_day = hdf[f'{met_group}/SWGDN_daytime'][:][:,sites]
        sw_rad_night = hdf[f'{met_group}/SWGDN_nighttime'][:][:,sites]
        temp_day = hdf[f'{met_group}/T10M_daytime'][:][:,sites]
        temp_night = hdf[f'{met_group}/T10M_nighttime'][:][:,sites]
        tmin = hdf[f'{met_group}/Tmin'][:][:,sites]
        # As long as the time series is balanced w.r.t. years (i.e., same
        #   number of records per year), the overall mean is the annual mean
        temp_annual = hdf[f'{met_group}/T10M'][:][:,sites].mean(axis = 0)[None,:]\
            .repeat(tmin.shape[0], axis = 0)
        vpd_day = MOD16.vpd(
            hdf[f'{met_group}/QV10M_daytime'][:][:,sites],
            hdf[f'{met_group}/PS_daytime'][:][:,sites],
            temp_day)
        vpd_night = MOD16.vpd(
            hdf[f'{met_group}/QV10M_nighttime'][:][:,sites],
            hdf[f'{met_group}/PS_nighttime'][:][:,sites],
            temp_night)
        # After VPD is calculated, air pressure is based solely
        #   on elevation
        elevation = hdf[CONFIG['data']['datasets']['elevation']][:]
        elevation = elevation[np.newaxis,:]\
            .repeat(nsteps, axis = 0)[:,sites]
        pressure = MOD16.air_pressure(elevation.mean(axis = -1))
        # Read in fPAR, LAI, and convert from (%) to [0,1]
        fpar = np.nanmean(
            hdf[CONFIG['data']['datasets']['fPAR']][:][:,sites], axis = -1)
        lai = np.nanmean(
            hdf[CONFIG['data']['datasets']['LAI']][:][:,sites], axis = -1)
        # Convert fPAR from (%) to [0,1] and re-scale LAI; reshape fPAR and LAI
        fpar /= 100
        lai /= 10
        tower_obs = hdf['FLUXNET/latent_heat'][:][:,sites]
        if pft is None:
            is_test = hdf['FLUXNET/validation_mask'][:].sum(axis = 0).astype(bool)
    # Compile driver datasets
    drivers = [
        lw_net_day, lw_net_night, sw_rad_day, sw_rad_night, sw_albedo,
        temp_day, temp_night, temp_annual, tmin, vpd_day, vpd_night,
        pressure, fpar, lai
    ]
    # Speed things up by focusing only on data points where valid data exist
    mask = ~np.isnan(tower_obs)
    if pft is None and validation_mask_only:
        # Stratify the data using the validation mask so that an equal number
        #   of samples from each PFT are used
        mask = np.logical_and(is_test, mask)
    drivers = [d[mask] for d in drivers]
    return (drivers, tower_obs[mask])


if __name__ == '__main__':
    import fire
    fire.Fire(main)
