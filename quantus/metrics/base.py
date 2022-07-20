"""This module implements the base class for creating evaluation measures."""
import warnings
from abc import abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm

from ..helpers import utils
from ..helpers import asserts
from ..helpers.model_interface import ModelInterface
from ..helpers.normalise_func import normalise_batch, normalise_by_negative
from ..helpers import warn_func


class Metric:
    """
    Implementation base Metric class.
    """

    @asserts.attributes_check
    def __init__(
            self,
            abs: bool = False,
            normalise: bool = False,
            normalise_func: Optional[Callable] = None,
            normalise_func_kwargs: Optional[Dict[str, Any]] = None,
            softmax: bool = False,
            default_plot_func: Optional[Callable] = None,
            disable_warnings: bool = False,
            display_progressbar: bool = False,
            **kwargs):
        """
        Initialise the Metric base class.

        Each of the defined metrics in Quantus, inherits from Metric base class.

        Parameters
        ----------
        abs (boolean): Indicates whether absolute operation is applied on the attribution, default=False.
        normalise (boolean): Indicates whether normalise operation is applied on the attribution, default=True.
        normalise_func (callable): Attribution normalisation function applied in case normalise=True.
            If normalise_func=None, the default value is used, default=normalise_by_negative.
        normalise_func_kwargs (dict): Keyword arguments to be passed to normalise_func on call, default={}.
        softmax (boolean): Indicates wheter to use softmax probabilities or logits in model prediction, default=False.
        default_plot_func (callable): Callable that plots the metrics result.
        disable_warnings (boolean): Indicates whether the warnings are printed, default=False.
        display_progressbar (boolean): Indicates whether a tqdm-progress-bar is printed, default=False.

        """
        self.abs = abs
        self.normalise = normalise
        self.softmax = softmax

        if normalise_func is None:
            self.normalise_func = normalise_by_negative
        self.normalise_func = normalise_func
        if normalise_func_kwargs is None:
            normalise_func_kwargs = {}
        # TODO: deprecate this kind of unspecific kwargs passing
        # this code prioritizes normalise_func_kwargs items over kwargs items
        normalise_func_kwargs = {**kwargs, **normalise_func_kwargs}
        self.normalise_func_kwargs = normalise_func_kwargs

        self.default_plot_func = default_plot_func
        self.disable_warnings = disable_warnings
        self.display_progressbar = display_progressbar

        self.last_results = []
        self.all_results = []

        # TODO: deprecate this kind of unspecific kwargs passing
        self.kwargs = kwargs

    def __call__(
            self,
            model,
            x_batch: np.ndarray,
            y_batch: Optional[np.ndarray] = None,
            a_batch: Optional[np.ndarray] = None,
            s_batch: Optional[np.ndarray] = None,
            channel_first: Optional[bool] = None,
            explain_func: Optional[Callable] = None,
            explain_func_kwargs: Optional[Dict[str, Any]] = None,
            model_predict_kwargs: Optional[Dict] = None,
            device: Optional[str] = None,
            softmax: Optional[bool] = None,
            **kwargs,
    ) -> Union[int, float, list, dict, None]:
        """
        TODO: add more specific documentation for inheriting from this class.

        This implementation represents the main logic of the metric and makes the class object callable.
        It completes batch-wise evaluation of some explanations (a_batch) with respect to some input data
        (x_batch), some output labels (y_batch) and a torch model (model).

        Parameters
        ----------
        model: a torch model e.g., torchvision.models that is subject to explanation
        x_batch: a np.ndarray which contains the input data that are explained
        y_batch: a np.ndarray which contains the output labels that are explained
        a_batch: a Union[np.ndarray, None] which contains pre-computed attributions i.e., explanations
        s_batch: a Union[np.ndarray, None] which contains segmentation masks that matches the input
        channel_first (boolean, optional): Indicates of the image dimensions are channel first, or channel last.
            Inferred from the input shape if None, default=None.
        explain_func (callable): Callable generating attributions, default=Callable.
        explain_func_kwargs (dict, optional): Keyword arguments to be passed to explain_func on call, default = {}
        device (string): Indicated the device on which a torch.Tensor is or will be allocated: "cpu" or "gpu",
            default=None.
        softmax (boolean): Indicates wheter to use softmax probabilities or logits in model prediction.
            This is used for this __call__ only and won't be saved as attribute. If None, self.softmax is used,
            default=None.
        model_predict_kwargs (dict, optional): Keyword arguments to be passed to the model's predict method, default = {}
        kwargs: Keyword arguments (optional)

        Returns
        -------
        last_results: a list of float(s) with the evaluation outcome of concerned batch

        Examples
        --------
        # Enable GPU.
        >> device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        # Load a pre-trained LeNet classification model (architecture at quantus/helpers/models).
        >> model = LeNet()
        >> model.load_state_dict(torch.load("tutorials/assets/mnist"))

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
        model, x_batch, y_batch, a_batch, s_batch = self.general_preprocess(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            a_batch=a_batch,
            s_batch=s_batch,
            channel_first=channel_first,
            explain_func=explain_func,
            explain_func_kwargs=explain_func_kwargs,
            softmax=softmax,
            device=device,
            model_predict_kwargs=model_predict_kwargs,
            **kwargs,
        )

        # Create progress bar if desired.
        iterator = tqdm(
            enumerate(zip(x_batch, y_batch, a_batch, s_batch)),
            total=len(x_batch),
            disable=not self.display_progressbar,
        )
        self.last_results = [None for _ in x_batch]
        for instance_id, (x_instance, y_instance, a_instance, s_instance) in iterator:
            result = self.evaluate_instance(
                model=model,
                x=x_instance,
                y=y_instance,
                a=a_instance,
                s=s_instance,
            )
            self.last_results[instance_id] = result

        # Call post-processing
        self.custom_postprocess(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            a_batch=a_batch,
            s_batch=s_batch,
        )

        self.all_results.append(self.last_results)
        return self.last_results

    @abstractmethod
    def evaluate_instance(
            self,
            model: ModelInterface,
            x_instance: np.ndarray,
            y_instance: Optional[np.ndarray] = None,
            a_instance: Optional[np.ndarray] = None,
            s_instance: Optional[np.ndarray] = None,
    ):
        '''
        TODO: add documentation.
        '''
        raise NotImplementedError()

    def general_preprocess(
            self,
            model,
            x_batch: np.ndarray,
            y_batch: Optional[np.ndarray] = None,
            a_batch: Optional[np.ndarray] = None,
            s_batch: Optional[np.ndarray] = None,
            channel_first: Optional[bool] = None,
            explain_func: Optional[Callable] = None,
            explain_func_kwargs: Optional[Dict[str, Any]] = None,
            softmax: bool = False,
            device: Optional[str] = None,
            model_predict_kwargs: Optional[Dict] = None,
            **kwargs,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, ModelInterface, Dict[str, Any]]:
        '''
        TODO: add documentation.
        '''
        # Reshape input batch to channel first order:
        if not isinstance(channel_first, bool):  # None is not a boolean instance.
            channel_first = utils.infer_channel_first(x_batch)
        x_batch = utils.make_channel_first(x_batch, channel_first)

        # Wrap the model into an interface
        if model:
            # Use attribute value if not passed explicitely.
            if softmax is None:
                softmax = self.softmax
            model = utils.get_wrapped_model(
                model=model,
                channel_first=channel_first,
                softmax=softmax,
                device=device,
                predict_kwargs=model_predict_kwargs,
            )

        # Update kwargs.
        # TODO: deprecate this kind of unspecific kwargs passing
        # TODO: Also state in the docstring, that passing kwargs will currently
        # overwrite the internal kwargs-attribute from the __init__ call.
        # This side-effect should be documented as it won't be expected by everyone.
        self.kwargs = {
            **kwargs,
            **{
                k: v for k, v in self.__dict__.items()
                if k not in ["args", "kwargs"]
            },
        }

        # Run deprecation warnings.
        warn_func.deprecation_warnings(self.kwargs)

        # Save as attribute, some metrics need it during processing.
        self.explain_func = explain_func
        if explain_func_kwargs is None:
            explain_func_kwargs = {}
        # TODO: deprecate this kind of unspecific kwargs passing
        # this code prioritizes explain_func_kwargs items over kwargs items
        self.explain_func_kwargs = {**kwargs, **explain_func_kwargs}

        if a_batch is None:

            # Asserts.
            asserts.assert_explain_func(explain_func=self.explain_func)

            # Generate explanations.
            a_batch = self.explain_func(
                model=model.get_model(),
                inputs=x_batch,
                targets=y_batch,
                **self.explain_func_kwargs,
            )

        # Expand attributions to input dimensionality.
        a_batch = utils.expand_attribution_channel(a_batch, x_batch)

        # Asserts.
        asserts.assert_attributions(x_batch=x_batch, a_batch=a_batch)

        # Call custom pre-processing from inheriting class.
        model, x_batch, y_batch, a_batch, s_batch = self.custom_preprocess(
            model=model,
            x_batch=x_batch,
            y_batch=y_batch,
            a_batch=a_batch,
            s_batch=s_batch,
        )

        # Normalise with specified keyword arguments if requested.
        if self.normalise:
            a_batch = normalise_batch(
                arr=a_batch,
                normalise_func=self.normalise_func,
                **self.normalise_func_kwargs,
            )

        # Take absolute if requested.
        if self.abs:
            a_batch = np.abs(a_batch)

        # This is needed for iterator (zipped over x_batch, y_batch, a_batch, s_batch)
        if s_batch is None:
            s_batch = [None for _ in x_batch]

        return model, x_batch, y_batch, a_batch, s_batch

    def custom_preprocess(
            self,
            model: ModelInterface,
            x_batch: np.ndarray,
            y_batch: Optional[np.ndarray],
            a_batch: Optional[np.ndarray],
            s_batch: np.ndarray,
    ) -> Tuple[ModelInterface, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return model, x_batch, y_batch, a_batch, s_batch

    def custom_postprocess(
            self,
            model: ModelInterface,
            x_batch: np.ndarray,
            y_batch: Optional[np.ndarray],
            a_batch: Optional[np.ndarray],
            s_batch: np.ndarray,
    ) -> Tuple[ModelInterface, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        pass

    @property
    def interpret_scores(self) -> None:
        """

        Returns
        -------

        """
        print(self.__init__.__doc__.split(".")[1].split("References")[0])
        # print(self.__call__.__doc__.split("callable.")[1].split("Parameters")[0])

    @property
    def get_params(self) -> dict:
        """
        List parameters of metric.
        Returns: a dictionary with attributes if not excluded from pre-determined list
        -------

        """
        attr_exclude = [
            "args",
            "kwargs",
            "all_results",
            "last_results",
            "default_plot_func",
            "disable_warnings",
            "display_progressbar",
        ]
        return {k: v for k, v in self.__dict__.items() if k not in attr_exclude}

    def set_params(self, key: str, value: Any) -> dict:
        """
        Set a parameter of a metric.
        Parameters
        ----------
        key: attribute of metric to mutate
        value: value to update the key with

        -------
        Returns: the updated dictionary.

        """
        self.kwargs[key] = value
        return self.kwargs

    def plot(
        self,
        plot_func: Union[Callable, None] = None,
        show: bool = True,
        path_to_save: Union[str, None] = None,
        *args,
        **kwargs,
    ) -> None:
        """
        Basic plotting functionality for Metric class.
        The user provides a plot_func (Callable) that contains the actual plotting logic (but returns None).

        Parameters
        ----------
        plot_func: a Callable with the actual plotting logic.
        show: a boolean to state if the plot shall be shown.
        path_to_save: a string that specifies the path to save file.
        args: an optional with additional arguments.
        kwargs: an optional dict with additional arguments.

        Returns: None.
        -------

        """

        # Get plotting func if not provided.
        if plot_func is None:
            plot_func = self.default_plot_func

        # Asserts.
        asserts.assert_plot_func(plot_func=plot_func)

        # Plot!
        plot_func(*args, **kwargs)

        if show:
            plt.show()

        if path_to_save:
            plt.savefig(fname=path_to_save, dpi=400)

        return None


class PerturbationMetric(Metric):
    """
    Implementation base PertubationMetric class.
    """

    @asserts.attributes_check
    def __init__(
            self,
            abs: bool = False,
            normalise: bool = False,
            normalise_func: Optional[Callable] = None,
            normalise_func_kwargs: Optional[Dict[str, Any]] = None,
            perturb_func: Callable = None,  # TODO: specify expected function signature
            perturb_func_kwargs: Optional[Dict[str, Any]] = None,
            default_plot_func: Optional[Callable] = None,
            disable_warnings: bool = False,
            display_progressbar: bool = False,
            **kwargs):

        # Initialize super-class with passed parameters
        super().__init__(
            abs=abs,
            normalise=normalise,
            normalise_func=normalise_func,
            normalise_func_kwargs=normalise_func_kwargs,
            default_plot_func=default_plot_func,
            display_progressbar=display_progressbar,
            disable_warnings=disable_warnings,
            **kwargs,
        )

        self.perturb_func = perturb_func

        if perturb_func_kwargs is None:
            perturb_func_kwargs = {}
        # TODO: deprecate this kind of unspecific kwargs passing
        # this code prioritizes perturb_func_kwargs items over kwargs items
        perturb_func_kwargs = {**kwargs, **perturb_func_kwargs}
        self.perturb_func_kwargs = perturb_func_kwargs
