# UI/data_access.py
# This file is a "dispatcher" that routes
# requests to the correct model-specific logic.

from . import model_graphcast
from . import model_gfs
from . import model_aigfs
from . import model_navgem_graphcast  # NEW
from . import model_atlas

def get_model_dataset(model_name: str, model_date: str, model_time: str, fhr: int, var: str, level: int | None):
    """Dispatches to the correct model's get_dataset function."""
    if model_name == "gfs":
        return model_gfs.get_dataset(model_date, model_time, fhr, var, level)
    elif model_name == "graphcast":
        return model_graphcast.get_dataset(model_date, model_time, fhr, var, level)
    elif model_name == "aigfs":
        return model_aigfs.get_dataset(model_date, model_time, fhr, var, level)
    elif model_name == "navgem-graphcast":  
        return model_navgem_graphcast.get_dataset(model_date, model_time, fhr, var, level)
    elif model_name == "atlas-gfs":  
        return model_atlas.get_dataset(model_date, model_time, fhr, var, level)
    else:
        # Default to GraphCast
        print(f"Warning: Unknown model '{model_name}', defaulting to GraphCast.")
        return model_graphcast.get_dataset(model_date, model_time, fhr, var, level)

def get_model_levels(model_name: str, model_date: str, model_time: str, var: str):
    """Dispatches to the correct model's get_levels function."""
    if model_name == "gfs":
        return model_gfs.get_levels(model_date, model_time, var)
    elif model_name == "graphcast":
        return model_graphcast.get_levels(model_date, model_time, var)
    elif model_name == "aigfs":
        return model_aigfs.get_levels(model_date, model_time, var)
    elif model_name == "navgem-graphcast": 
        return model_navgem_graphcast.get_levels(model_date, model_time, var)
    elif model_name == "atlas-gfs": 
        return model_atlas.get_levels(model_date, model_time, var)
    else:
        # Default to GraphCast
        return model_graphcast.get_levels(model_date, model_time, var)