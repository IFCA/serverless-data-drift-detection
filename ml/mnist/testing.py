import argparse
import logging
import pickle
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torchvision

from ml.mnist.utils.dr import Encoder, encode_data
from ml.mnist.utils.drift import make_transformed_dataset, save_images
from ml.mnist.utils.drift import transformations
from ml.mnist.utils.model import CNN, test_model


def main(
        test_images_dir: str,
        encoder_file_path: Path,
        detector_file_path: Path,
        model_batch_size: int,
        model_file_path: Path,
        transform_file_path: Path,
        alpha: float,
) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    logger.info("Loading transform...")
    transform = torch.load(f=transform_file_path)

    test_dataset = torchvision.datasets.ImageFolder(
        root=test_images_dir,
        transform=transform,
    )

    data_loaders = []
    test_data_loader = torch.utils.data.DataLoader(  # 10000 samples
        dataset=test_dataset,
        batch_size=model_batch_size,
        shuffle=False,
    )
    data_loaders.append(("Reference", test_data_loader))

    for type_, transformation in transformations:
        dataset = make_transformed_dataset(
            subset=test_dataset,
            transform=transformation,
        )
        data_loader = torch.utils.data.DataLoader(
            dataset=dataset,
            batch_size=model_batch_size,
            shuffle=False,
        )
        data_loaders.append((type_, data_loader))

    for type_, data_loader in data_loaders:
        save_images(
            data_loader=data_loader,
            target_dir=Path("data", type_),
        )

    logger.info("Loading encoder...")
    encoder_state_dict = torch.load(
        f=encoder_file_path,
    )
    latent_dim = [*encoder_state_dict.items()][-1][-1].size(dim=0)
    encoder = Encoder(
        latent_dim=latent_dim,
    ).to(device)
    encoder.eval()
    encoder.load_state_dict(
        state_dict=encoder_state_dict,
    )
    logger.info("Applying dimensionality reduction...")

    X_encoded = []
    for type_, data_loader in data_loaders:
        X, y = encode_data(
            encoder=encoder,
            data_loader=data_loader,
        )
        X_encoded.append((type_, X))

    logger.info("Loading drift detector...")
    detector = load_obj(path=detector_file_path)
    # FIXME: This is a hack to change the number of permutations
    # detector.callbacks[0].num_permutations = 20

    logger.info("Checking for drift...")
    for type_, X in X_encoded:
        data_drift_check = check_drift(
            detector=detector,
            X=X,
            alpha=alpha,
        )
        logger.info(f"{type_} data drift check: {data_drift_check}")

    logger.info("Loading model...")
    model = CNN().to(device)
    model.eval()
    model_state_dict = torch.load(
        f=model_file_path,
    )
    for k, _v in model_state_dict.copy().items():
        model_state_dict[k.removeprefix("_orig_mod.")] = model_state_dict.pop(k)
    model.load_state_dict(state_dict=model_state_dict)

    logger.info("Testing model...")
    for type_, data_loader in data_loaders:
        accuracy = test_model(
            model=model,
            data_loader=data_loader,
        )
        logger.info(f"{type_} accuracy on test set: {accuracy:.4f}")


def check_drift(detector, X: np.ndarray, alpha: float) -> dict[str, Any]:
    distance, callback_logs = detector.compare(X=X)
    return {
        "is_drift": callback_logs["permutation_test"]["p_value"] < alpha,
        "distance": distance,
        "p_value": callback_logs["permutation_test"]["p_value"],
    }


def load_obj(path: Path) -> Any:
    with open(path, "rb") as file:
        obj = pickle.load(
            file=file,
        )
    return obj


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s,%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d:%H:%M:%S",
        level=logging.INFO,
    )
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="MNIST testing.")
    parser.add_argument("-ti", "--TestImagesDir", type=str, help="Test images directory", default="data/test")
    parser.add_argument("-mb", "--ModelBatchSize", type=int, help="Model batch size", default=64)
    parser.add_argument("-mf", "--ModelFilePath", type=str, help="Model file path", default="objects/model.pt")
    parser.add_argument("-df", "--DetectorFilePath", type=str, help="Detector file path", default="objects/detector.pkl")
    parser.add_argument("-ef", "--EncoderFilePath", type=str, help="Encoder file path", default="objects/encoder.pt")
    parser.add_argument("-tf", "--TransformFilePath", type=str, help="Transform file path", default="objects/transformer.pt")
    parser.add_argument("-a", "--Alpha", type=float, help="Alpha", default=0.001)

    args = parser.parse_args()

    main(
        test_images_dir=args.TestImagesDir,
        model_batch_size=args.ModelBatchSize,
        model_file_path=Path(args.ModelFilePath),
        detector_file_path=Path(args.DetectorFilePath),
        encoder_file_path=Path(args.EncoderFilePath),
        transform_file_path=Path(args.TransformFilePath),
        alpha=args.Alpha,
    )
