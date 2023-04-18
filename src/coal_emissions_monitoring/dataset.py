from pathlib import Path
from typing import Optional, Union
import torch
from torch.utils.data import IterableDataset, DataLoader
from lightning import LightningDataModule
import geopandas as gpd

from coal_emissions_monitoring.constants import (
    BATCH_SIZE,
    EMISSIONS_TARGET,
    FINAL_COLUMNS,
    IMAGE_SIZE_PX,
    TEST_YEAR,
    TRAIN_VAL_RATIO,
)
from coal_emissions_monitoring.satellite_imagery import (
    get_image_from_cog,
    is_image_too_dark,
)
from coal_emissions_monitoring.data_cleaning import get_final_dataset
from coal_emissions_monitoring.ml_utils import (
    get_facility_set_mapper,
    split_data_in_sets,
)
from coal_emissions_monitoring.transforms import (
    train_transforms,
    val_transforms,
    test_transforms,
)


class CoalEmissionsDataset(IterableDataset):
    def __init__(
        self,
        gdf: gpd.GeoDataFrame,
        target: str = EMISSIONS_TARGET,
        image_size: int = IMAGE_SIZE_PX,
        max_dark_frac: float = 0.5,
        transforms: Optional[torch.nn.Module] = None,
    ):
        """
        Dataset that gets images of coal power plants, their emissions
        and metadata.

        Args:
            gdf (gpd.GeoDataFrame):
                A GeoDataFrame with the following columns:
                - facility_id
                - facility_name
                - latitude
                - longitude
                - ts
                - co2_mass_short_tons
                - cloud_cover
                - cog_url
                - geometry
            target (str):
                The target column to predict
            image_size (int):
                The size of the image in pixels
            max_dark_frac (float):
                The maximum fraction of dark pixels allowed for an image;
                if the image has more dark pixels than this, it is skipped
            transforms (Optional[torch.nn.Module]):
                A PyTorch module that transforms the image
        """
        assert len(set(FINAL_COLUMNS) - set(gdf.columns)) == 0, (
            "gdf must have all columns of the following list:\n"
            f"{FINAL_COLUMNS}\n"
            f"Instead, gdf has the following columns:\n"
            f"{gdf.columns}"
        )
        self.gdf = gdf
        self.target = target
        self.image_size = image_size
        self.max_dark_frac = max_dark_frac
        self.transforms = transforms

    def __len__(self):
        return len(self.gdf)

    def __iter__(self):
        if torch.utils.data.get_worker_info():
            worker_total_num = torch.utils.data.get_worker_info().num_workers
            worker_id = torch.utils.data.get_worker_info().id
        else:
            worker_total_num = 1
            worker_id = 0
        for idx in range(worker_id, len(self), worker_total_num):
            row = self.gdf.iloc[idx]
            image = get_image_from_cog(
                cog_url=row.cog_url, geometry=row.geometry, size=self.image_size
            )
            image = torch.from_numpy(image).float()
            if is_image_too_dark(image, max_dark_frac=self.max_dark_frac):
                continue
            if self.transforms is not None:
                image = self.transforms(image).squeeze(0)
            target = torch.tensor(row[self.target]).float()
            metadata = row.drop([self.target, "geometry", "data_set"]).to_dict()
            metadata["ts"] = str(metadata["ts"])
            yield {
                "image": image,
                "target": target,
                "metadata": metadata,
            }


class CoalEmissionsDataModule(LightningDataModule):
    def __init__(
        self,
        image_metadata_path: Union[str, Path],
        campd_facilities_path: Union[str, Path],
        campd_emissions_path: Union[str, Path],
        target: str = EMISSIONS_TARGET,
        image_size: int = IMAGE_SIZE_PX,
        train_val_ratio: float = TRAIN_VAL_RATIO,
        test_year: int = TEST_YEAR,
        batch_size: int = BATCH_SIZE,
        num_workers: int = 0,
    ):
        """
        Lightning Data Module that gets images of coal power plants,
        their emissions and metadata, and splits them into train,
        validation and test sets.

        Args:
            image_metadata_path (Union[str, Path]):
                Path to image metadata data
            campd_facilities_path (Union[str, Path]):
                Path to CAMPD facilities data
            campd_emissions_path (Union[str, Path]):
                Path to CAMPD emissions data
            target (str):
                The target column to predict
            image_size (int):
                The size of the image in pixels
            train_val_ratio (float):
                The ratio of train to validation data
            test_year (int):
                The year to use for testing
            batch_size (int):
                The batch size, i.e. the number of samples to load at once
            num_workers (int):
                The number of workers to use for loading data
        """
        super().__init__()
        self.image_metadata_path = image_metadata_path
        self.campd_facilities_path = campd_facilities_path
        self.campd_emissions_path = campd_emissions_path
        self.target = target
        self.image_size = image_size
        self.train_val_ratio = train_val_ratio
        self.test_year = test_year
        self.batch_size = batch_size
        self.num_workers = num_workers

    def setup(self, stage: str):
        """
        Split the data into train, validation and test sets.

        Args:
            stage (str):
                The stage of the setup
        """
        gdf = get_final_dataset(
            image_metadata_path=self.image_metadata_path,
            campd_facilities_path=self.campd_facilities_path,
            campd_emissions_path=self.campd_emissions_path,
        )
        facility_set_mapper = get_facility_set_mapper(
            campd_facilities_path=self.campd_facilities_path,
            train_val_ratio=self.train_val_ratio,
        )
        gdf["data_set"] = gdf.apply(
            lambda row: split_data_in_sets(
                row=row, data_set_mapper=facility_set_mapper, test_year=self.test_year
            ),
            axis=1,
        )
        if stage == "fit":
            self.train_dataset = CoalEmissionsDataset(
                gdf=gdf[gdf.data_set == "train"].sample(frac=1),
                target=self.target,
                image_size=self.image_size,
                transforms=train_transforms,
            )
            self.val_dataset = CoalEmissionsDataset(
                gdf=gdf[gdf.data_set == "val"].sample(frac=1),
                target=self.target,
                image_size=self.image_size,
                transforms=val_transforms,
            )
        elif stage == "test":
            self.test_dataset = CoalEmissionsDataset(
                gdf=gdf[gdf.data_set == "test"].sample(frac=1),
                target=self.target,
                image_size=self.image_size,
                transforms=test_transforms,
            )

    def train_dataloader(self):
        return DataLoader(
            self.train_dataset,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
        )

    def val_dataloader(self):
        return DataLoader(
            self.val_dataset,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
        )

    def test_dataloader(self):
        return DataLoader(
            self.test_dataset,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
        )