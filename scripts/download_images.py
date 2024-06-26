# %% [markdown]
# # Images download
# ---
#
# Download all images before training models.

# %% [markdown]
# ## Setup

# %% [markdown]
# ### Imports

# %%
import os
from tqdm.auto import tqdm

# %%
from coal_emissions_monitoring.constants import ALL_BANDS, MAIN_COLUMNS
from coal_emissions_monitoring.data_cleaning import load_final_dataset
from coal_emissions_monitoring.satellite_imagery import fetch_image_path_from_cog

# %% [markdown]
# ## Get final datase

# %%
gdf = load_final_dataset(
    "/content/all_urls_dataset.csv"
)

# %% [markdown]
# ## Download images

# %% [markdown]
# ### TCI (True Color Image)

# %%
tqdm.pandas(desc="Downloading visual images")
gdf["local_image_path"] = gdf.progress_apply(
    lambda row: fetch_image_path_from_cog(
        cog_url=row.visual,
        geometry=row.geometry,
        cog_type="visual",
        images_dir="/content",
        download_missing_images=True,
    ),
    axis=1,
)

# %%
path = "/content"
os.makedirs(path, exist_ok=True)
gdf.rename(columns={"visual": "cog_url"})[MAIN_COLUMNS + ["local_image_path"]].to_csv(
    f"{path}final_dataset.csv",
    index=False,
)

# %%
# compress all images into one file
os.system(
    "tar -czvf /content/visual_images.tar.gz /content"
)

# %% [markdown]
# ### All bands

# %%
tqdm.pandas(desc="Downloading all bands images")
gdf["local_image_all_bands_path"] = gdf.progress_apply(
    lambda row: fetch_image_path_from_cog(
        cog_url=[row[band] for band in ALL_BANDS],
        geometry=row.geometry,
        size=32,  # smaller images to make the download faster
        cog_type="all",
        images_dir="/content",
        download_missing_images=True,
    ),
    axis=1,
)

# %%
# compress all images into one file
os.system(
    "tar -czvf /content/all_bands_images.tar.gz /content"
)

# %%
path = "/content"
os.makedirs(path, exist_ok=True)
gdf.rename(columns={"visual": "cog_url"})[
    MAIN_COLUMNS + ["local_image_path", "local_image_all_bands_path"]
].to_csv(
    f"{path}final_dataset.csv",
    index=False,
)
