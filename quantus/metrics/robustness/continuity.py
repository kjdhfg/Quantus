"""This module contains the implementation of the Continuity metric."""

# This file is part of Quantus.
# Quantus is free software: you can redistribute it and/or modify it under the terms of the GNU Lesser General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
# Quantus is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more details.
# You should have received a copy of the GNU Lesser General Public License along with Quantus. If not, see <https://www.gnu.org/licenses/>.
# Quantus project URL: <https://github.com/understandable-machine-intelligence-lab/Quantus>.

import itertools
from typing import Any, Callable, Dict, List, Optional
import numpy as np
import torch

from quantus.helpers import asserts
from quantus.helpers import utils
from quantus.helpers import warn
from quantus.helpers.model.model_interface import ModelInterface
from quantus.functions.normalise_func import normalise_by_max
from quantus.functions.perturb_func import translation_x_direction
from quantus.functions.similarity_func import lipschitz_constant, correlation_pearson
from quantus.metrics.base import PerturbationMetric


class Continuity(PerturbationMetric):
    """
    Implementation of the Continuity test by Montavon et al., 2018.

    The test measures the strongest variation of the explanation in the input domain i.e.,
    ||R(x) - R(x')||_1 / ||x - x'||_2
    where R(x) is the explanation for input x and x' is the perturbed input.

    Assumptions:
        - The original metric definition relies on perturbation functionality suited only for images.
        Therefore, only apply the metric to 3-dimensional (image) data. To extend the applicablity
        to other data domains, adjustments to the current implementation might be necessary.

    References:
        1) Grégoire Montavon et al.: "Methods for interpreting and
        understanding deep neural networks." Digital Signal Processing 73 (2018): 1-15.

    """

    @asserts.attributes_check
    def __init__(
        self,
        similarity_func: Optional[Callable] = None,
        nr_steps: int = 28,
        patch_size: int = 7,
        abs: bool = True,
        modality="Image",
        normalise: bool = True,
        normalise_func: Optional[Callable[[np.ndarray], np.ndarray]] = None,
        normalise_func_kwargs: Optional[Dict[str, Any]] = None,
        perturb_func: Callable = None,
        perturb_baseline: str = "black",
        perturb_func_kwargs: Optional[Dict[str, Any]] = None,
        return_aggregate: bool = False,
        aggregate_func: Callable = np.mean,
        default_plot_func: Optional[Callable] = None,
        disable_warnings: bool = False,
        display_progressbar: bool = False,
        return_nan_when_prediction_changes: bool = False,
        **kwargs,
    ):
        """
        Parameters
            ----------
        similarity_func: callable
            Similarity function applied to compare input and perturbed input.
            If None, the default value is used, default=difference.
        patch_size: integer
            The patch size for masking, default=7.
        nr_steps: integer
            The number of steps to iterate over, default=28.
        abs: boolean
            Indicates whether absolute operation is applied on the attribution, default=True.
        normalise: boolean
            Indicates whether normalise operation is applied on the attribution, default=True.
        normalise_func: callable
            Attribution normalisation function applied in case normalise=True.
            If normalise_func=None, the default value is used, default=normalise_by_max.
        normalise_func_kwargs: dict
            Keyword arguments to be passed to normalise_func on call, default={}.
        perturb_func: callable
            Input perturbation function. If None, the default value is used,
            default=translation_x_direction.
        perturb_baseline: string
            Indicates the type of baseline: "mean", "random", "uniform", "black" or "white",
            default="black".
        perturb_func_kwargs: dict
            Keyword arguments to be passed to perturb_func, default={}.
        return_aggregate: boolean
            Indicates if an aggregated score should be computed over all instances.
        aggregate_func: callable
            Callable that aggregates the scores given an evaluation call.
        default_plot_func: callable
            Callable that plots the metrics result.
        disable_warnings: boolean
            Indicates whether the warnings are printed, default=False.
        display_progressbar: boolean
            Indicates whether a tqdm-progress-bar is printed, default=False.
        default_plot_func: callable
            Callable that plots the metrics result.
        return_nan_when_prediction_changes: boolean
            When set to true, the metric will be evaluated to NaN if the prediction changes after the perturbation is applied.
        kwargs: optional
            Keyword arguments.
        """
        self.modality = modality
        if normalise_func is None:
            normalise_func = normalise_by_max

        if perturb_func is None:
            perturb_func = translation_x_direction

        if perturb_func_kwargs is None:
            perturb_func_kwargs = {}
        perturb_func_kwargs["perturb_baseline"] = perturb_baseline

        super().__init__(
            abs=abs,
            normalise=normalise,
            normalise_func=normalise_func,
            normalise_func_kwargs=normalise_func_kwargs,
            perturb_func=perturb_func,
            perturb_func_kwargs=perturb_func_kwargs,
            return_aggregate=return_aggregate,
            aggregate_func=aggregate_func,
            default_plot_func=default_plot_func,
            display_progressbar=display_progressbar,
            disable_warnings=disable_warnings,
            **kwargs,
        )

        # Save metric-specific attributes.
        if similarity_func is None:
            similarity_func = correlation_pearson
        self.similarity_func = similarity_func
        self.patch_size = patch_size
        self.nr_steps = nr_steps
        self.nr_patches: Optional[int] = None
        self.dx = None
        self.return_nan_when_prediction_changes = return_nan_when_prediction_changes

        # Asserts and warnings.
        if not self.disable_warnings:
            warn.warn_parameterisation(
                metric_name=self.__class__.__name__,
                sensitive_params=(
                    "how many patches to split the input image to 'nr_patches', "
                    "the number of steps to iterate over 'nr_steps', the value to replace"
                    " the masking with 'perturb_baseline' and in what direction to "
                    "translate the image 'perturb_func'"
                ),
                data_domain_applicability=(
                    f"Also, the current implementation only works for 3-dimensional (image) data."
                ),
                citation=(
                    "Montavon, Grégoire, Wojciech Samek, and Klaus-Robert Müller. 'Methods for "
                    "interpreting and understanding deep neural networks.' Digital Signal "
                    "Processing 73, 1-15 (2018"
                ),
            )

    def __call__(
        self,
        model,
        x_batch: np.array,
        y_batch: np.array,
        a_batch: Optional[np.ndarray] = None,
        s_batch: Optional[np.ndarray] = None,
        channel_first: Optional[bool] = None,
        explain_func: Optional[Callable] = None,
        explain_func_kwargs: Optional[Dict] = None,
        model_predict_kwargs: Optional[Dict] = None,
        softmax: Optional[bool] = False,
        device: Optional[str] = None,
        batch_size: int = 64,
        custom_batch: Optional[Any] = None,
        **kwargs,
    ) -> List[float]:
        """
        This implementation represents the main logic of the metric and makes the class object callable.
        It completes instance-wise evaluation of explanations (a_batch) with respect to input data (x_batch),
        output labels (y_batch) and a torch or tensorflow model (model).

        Calls general_preprocess() with all relevant arguments, calls
        () on each instance, and saves results to last_results.
        Calls custom_postprocess() afterwards. Finally returns last_results.

        Parameters
        ----------
        model: torch.nn.Module, tf.keras.Model
            A torch or tensorflow model that is subject to explanation.
        x_batch: np.ndarray
            A np.ndarray which contains the input data that are explained.
        y_batch: np.ndarray
            A np.ndarray which contains the output labels that are explained.
        a_batch: np.ndarray, optional
            A np.ndarray which contains pre-computed attributions i.e., explanations.
        s_batch: np.ndarray, optional
            A np.ndarray which contains segmentation masks that matches the input.
        channel_first: boolean, optional
            Indicates of the image dimensions are channel first, or channel last.
            Inferred from the input shape if None.
        explain_func: callable
            Callable generating attributions.
        explain_func_kwargs: dict, optional
            Keyword arguments to be passed to explain_func on call.
        model_predict_kwargs: dict, optional
            Keyword arguments to be passed to the model's predict method.
        softmax: boolean
            Indicates whether to use softmax probabilities or logits in model prediction.
            This is used for this __call__ only and won't be saved as attribute. If None, self.softmax is used.
        device: string
            Indicated the device on which a torch.Tensor is or will be allocated: "cpu" or "gpu".
        kwargs: optional
            Keyword arguments.

        Returns
        -------
        last_results: list
            a list of Any with the evaluation scores of the concerned batch.

        Examples:
        --------
            # Minimal imports.
            >> import quantus
            >> from quantus import LeNet
            >> import torch

            # Enable GPU.
            >> device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

            # Load a pre-trained LeNet classification model (architecture at quantus/helpers/models).
            >> model = LeNet()
            >> model.load_state_dict(torch.load("tutorials/assets/pytests/mnist_model"))

            # Load MNIST datasets and make loaders.
            >> test_set = torchvision.datasets.MNIST(root='./sample_data', download=True)
            >> test_loader = torch.utils.data.DataLoader(test_set, batch_size=24)

            # Load a batch of inputs and outputs to use for XAI evaluation.
            >> x_batch, y_batch = iter(test_loader).next()
            >> x_batch, y_batch = x_batch.cpu().numpy(), y_batch.cpu().numpy()

            # Generate Saliency attributions of the test set batch of the test set.
            >> a_batch_saliency = Saliency(model).attribute(inputs=x_batch, target=y_batch, abs=True).sum(axis=1)
            >> a_batch_saliency = a_batch_saliency.cpu().numpy()

            # Initialise the metric and evaluate explanations by calling the metric instance.
            >> metric = Metric(abs=True, normalise=False)
            >> scores = metric(model=model, x_batch=x_batch, y_batch=y_batch, a_batch=a_batch_saliency}
        """
        self.device = device
        return super().__call__(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            a_batch=a_batch,
            s_batch=s_batch,
            custom_batch=None,
            channel_first=channel_first,
            explain_func=explain_func,
            explain_func_kwargs=explain_func_kwargs,
            softmax=softmax,
            device=device,
            model_predict_kwargs=model_predict_kwargs,
            **kwargs,
        )

    def evaluate_instance(
        self,
        model: ModelInterface,
        x: np.ndarray,
        y: np.ndarray,
        a: np.ndarray,
        s: np.ndarray,
    ) -> Dict:
        """
        Evaluate instance gets model and data for a single instance as input and returns the evaluation result.

        Parameters
        ----------
        model: ModelInterface
            A ModelInteface that is subject to explanation.
        x: np.ndarray
            The input to be evaluated on an instance-basis.
        y: np.ndarray
            The output to be evaluated on an instance-basis.
        a: np.ndarray
            The explanation to be evaluated on an instance-basis.
        s: np.ndarray
            The segmentation to be evaluated on an instance-basis.

        Returns
        -------
        dict
            The evaluation results.
        """

        results: Dict[int, list] = {k: [] for k in range((self.nr_patches) + 1)}

        dx_max = self.dx * self.nr_steps

        for step in range(self.nr_steps):
            # Generate explanation based on perturbed input x.
            dx_step = (step + 1) * self.dx
            x_perturbed = self.perturb_func(
                arr=x,
                indices=np.arange(0, x.size),
                indexed_axes=np.arange(0, x.ndim),
                perturb_dx=dx_step,
                dx_max=dx_max,
                **self.perturb_func_kwargs,
            )
            x_input = model.shape_input(x_perturbed, x.shape, channel_first=True)

            prediction_changed = (
                model.predict(np.expand_dims(x, 0)).argmax(axis=-1)[0]
                != model.predict(x_input).argmax(axis=-1)[0]
                if self.return_nan_when_prediction_changes
                else False
            )

            # Generate explanation based on perturbed input x.
            a_perturbed = self.explain_func(
                inputs=torch.tensor(x_input).to(self.device)
                if x_input.shape[1] <= 3
                else torch.tensor(x_input).unsqueeze(1).to(self.device),
                target=torch.tensor(y).to(self.device),
                **self.explain_func_kwargs,
            )

            if torch.is_tensor(a_perturbed):
                a_perturbed = a_perturbed.cpu().detach().numpy()

            # Taking the first element, since a_perturbed will be expanded to a batch dimension
            # not expected by the current index management functions.
            if a.shape[0] <= 3:
                a_perturbed = utils.expand_attribution_channel(a_perturbed, x_input)[0]
            else:
                a_perturbed = a_perturbed.squeeze()

            if self.normalise:
                a_perturbed = self.normalise_func(
                    a_perturbed, **self.normalise_func_kwargs
                )

            if self.abs:
                a_perturbed = np.abs(a_perturbed)

            # Store the prediction score as the last element of the sub_self.last_results dictionary.
            y_pred = float(model.predict(x_input)[:, y])

            results[self.nr_patches].append(y_pred)

            # Create patches by splitting input into grid. Take x_input[0] to avoid batch axis,
            # which a_axes is not tuned for
            axis_iterators = [
                range(0, x_input[0].shape[axis], self.patch_size)
                for axis in self.a_axes
            ]

            for ix_patch, top_left_coords in enumerate(
                itertools.product(*axis_iterators)
            ):
                if prediction_changed:
                    results[ix_patch].append(np.nan)
                    continue

                if self.modality == "Image":
                    patch = (x_input[0].shape[0], self.patch_size, self.patch_size)
                elif self.modality == "Point_Cloud":
                    patch = (self.patch_size, 1)
                else:
                    patch = (self.patch_size, self.patch_size, self.patch_size)

                # Create slice for patch.
                patch_slice = utils.create_patch_slice(
                    patch_size=patch,
                    coords=top_left_coords,
                )

                a_perturbed_patch = a_perturbed[
                    utils.expand_indices(a_perturbed, patch_slice, self.a_axes)
                ]

                # Taking the first element, since a_perturbed will be expanded to a batch dimension
                # not expected by the current index management functions.
                # a_perturbed = utils.expand_attribution_channel(a_perturbed, x_input)[0]

                if self.normalise:
                    a_perturbed_patch = self.normalise_func(
                        a_perturbed_patch.flatten(), **self.normalise_func_kwargs
                    )

                if self.abs:
                    a_perturbed_patch = np.abs(a_perturbed_patch.flatten())

                # Sum attributions for patch.
                patch_sum = float(sum(a_perturbed_patch))
                results[ix_patch].append(patch_sum)

        return results

    def custom_preprocess(
        self,
        model: ModelInterface,
        x_batch: np.ndarray,
        y_batch: Optional[np.ndarray],
        a_batch: Optional[np.ndarray],
        s_batch: np.ndarray,
        custom_batch: Optional[np.ndarray],
    ) -> None:
        """
        Implementation of custom_preprocess_batch.

        Parameters
        ----------
        model: torch.nn.Module, tf.keras.Model
            A torch or tensorflow model e.g., torchvision.models that is subject to explanation.
        x_batch: np.ndarray
            A np.ndarray which contains the input data that are explained.
        y_batch: np.ndarray
            A np.ndarray which contains the output labels that are explained.
        a_batch: np.ndarray, optional
            A np.ndarray which contains pre-computed attributions i.e., explanations.
        s_batch: np.ndarray, optional
            A np.ndarray which contains segmentation masks that matches the input.
        custom_batch: any
            Gives flexibility ot the user to use for evaluation, can hold any variable.

        Returns
        -------
        None.
        """

        # Get number of patches for input shape (ignore batch and channel dim).
        if self.modality == "Image":
            self.nr_patches = utils.get_nr_patches(
                patch_size=self.patch_size,
                shape=x_batch.shape[2:],
                overlap=True,
            )

            self.dx = np.prod(x_batch.shape[2:]) // self.nr_steps
            asserts.assert_patch_size(
                patch_size=self.patch_size, shape=x_batch.shape[2:]
            )
        elif self.modality == "Point_Cloud":
            self.nr_patches = utils.get_nr_patches(
                patch_size=self.patch_size,
                shape=x_batch.shape[1:],
                overlap=False,
            )
            self.nr_patches += 1
            self.dx = np.prod(x_batch.shape[1:]) // self.nr_steps
            asserts.assert_patch_size(
                patch_size=self.patch_size, shape=x_batch.shape[1:]
            )
        else:
            self.nr_patches = int(x_batch.shape[1] ** 3 / self.patch_size**3)
            self.dx = np.prod(x_batch.shape[1:]) // self.nr_steps
            asserts.assert_patch_size(
                patch_size=self.patch_size, shape=x_batch.shape[1:]
            )

        # Asserts.
        # Additional explain_func assert, as the one in prepare() won't be
        # executed when a_batch != None.
        asserts.assert_explain_func(explain_func=self.explain_func)

    @property
    def aggregated_score(self):
        """
        Implements a continuity correlation score (an addition to the original method) to evaluate the
        relationship between change in explanation and change in function output. It can be seen as an
        quantitative interpretation of visually determining how similar f(x) and R(x1) curves are.
        """
        return [
            np.mean(
                [
                    np.nan_to_num(
                        self.similarity_func(
                            self.last_results[sample][self.nr_patches],
                            self.last_results[sample][ix_patch],
                        )
                    )
                    if np.sum(np.isnan(self.last_results[sample][ix_patch])) == 0
                    else 0
                    for ix_patch in range(self.nr_patches)
                ]
            )
            for sample in range(len(self.last_results))
        ]
