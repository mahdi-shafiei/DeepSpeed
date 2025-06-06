# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: Apache-2.0

# DeepSpeed Team
"""
Collection of DeepSpeed configuration utilities
"""
import collections
import json
import torch
from functools import reduce
from pydantic import BaseModel, ConfigDict, field_serializer

from deepspeed.utils import logger


class DeepSpeedConfigModel(BaseModel):
    """
    This class should be used as a base for all DeepSpeed configs. It extends
    pydantic.BaseModel to allow for deprecated fields. To enable this feature,
    add deprecated=True to pydantic.Field:

    my_dep_field: int = Field(0, deprecated=True)

    Deprecated Field kwargs:
    - deprecated: [True|False], default False
        Enables / Disables deprecated fields
    - deprecated_msg: str, default ""
        Message to include with deprecation warning
    - new_param: str, default ""
        Name of the field replacing the deprecated field
    - set_new_param: [True|False], default True
        If new_param is provided, enables setting the value of that param with
        deprecated field value
    - new_param_fn: callable, default (lambda x: x)
        If new_param is provided and set_new_param is True, this function will
        modify the value of the deprecated field before placing that value in
        the new_param field

    Example:
        my_new_field is replacing a deprecated my_old_field. The expected type
        for my_new_field is int while the expected type for my_old_field is
        str. We want to maintain backward compatibility with our configs, so we
        define the fields with:

        class MyExampleConfig(DeepSpeedConfigModel):
            my_new_field: int = 0
            my_old_field: str = Field('0',
                                      deprecated=True,
                                      new_param='my_new_field',
                                      new_param_fn=(lambda x: int(x)))
    """

    def __init__(self, strict=False, **data):
        if (not strict):  # This is temporary until we refactor all DS configs, allows HF to load models
            data = {k: v for k, v in data.items() if (v != "auto" or k == "replace_method")}
        super().__init__(**data)
        self._deprecated_fields_check()

    def _process_deprecated_field(self, dep_field):
        # Get information about the deprecated field
        pydantic_config = self
        fields_set = pydantic_config.model_fields_set
        kwargs = type(pydantic_config).model_fields[dep_field].json_schema_extra
        new_param_fn = kwargs.get("new_param_fn", lambda x: x)
        param_value = new_param_fn(getattr(pydantic_config, dep_field))
        new_field = kwargs.get("new_param", "")
        dep_msg = kwargs.get("deprecated_msg", "")
        if dep_field in fields_set:
            logger.warning(f"Config parameter {dep_field} is deprecated" +
                           (f" use {new_field} instead" if new_field else "") + (f". {dep_msg}" if dep_msg else ""))
            # Check if there is a new param and if it should be set with a value
            if new_field and kwargs.get("set_new_param", True):
                # Remove the deprecate field if there is a replacing field
                try:
                    delattr(pydantic_config, dep_field)
                except Exception as e:
                    logger.error(f"Tried removing deprecated '{dep_field}' from config")
                    raise e

                # Set new param value
                new_param_nested = new_field.split(".")
                if len(new_param_nested) > 1:
                    # If the new param exists in a subconfig, we need to get
                    # the fields set for that subconfig
                    pydantic_config = reduce(getattr, new_param_nested[:-1], pydantic_config)
                    fields_set = pydantic_config.model_fields_set
                new_param_name = new_param_nested[-1]
                assert (
                    new_param_name not in fields_set
                ), f"Cannot provide deprecated parameter '{dep_field}' and replacing parameter '{new_field}' together"
                # A custom function for converting the old param value to new param value can be provided
                try:
                    setattr(pydantic_config, new_param_name, param_value)
                except Exception as e:
                    logger.error(f"Tried setting value for '{new_field}' with value from deprecated '{dep_field}'")
                    raise e

    def _deprecated_fields_check(self):
        fields = type(self).model_fields
        for field_name, field_info in fields.items():
            if field_info.json_schema_extra and field_info.json_schema_extra.get("deprecated", False):
                self._process_deprecated_field(field_name)

    model_config = ConfigDict(
        validate_default=True,
        validate_assignment=True,
        use_enum_values=True,
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        protected_namespaces=(),
    )

    @field_serializer("dtype", check_fields=False)
    def serialize_torch_dtype(dtype: torch.dtype) -> str:
        return str(dtype)


def get_config_default(config, field_name):
    assert field_name in config.model_fields, f"'{field_name}' is not a field in {config}"
    assert not config.model_fields.get(
        field_name).is_required(), f"'{field_name}' is a required field and does not have a default value"
    return config.model_fields.get(field_name).get_default()


class pp_int(int):
    """
    A wrapper for integers that will return a custom string or comma-formatted
    string of the integer. For example, print(pp_int(1e5)) will return
    "10,000". This is useful mainly for auto-generated documentation purposes.
    """

    def __new__(cls, val, custom_print_str=None):
        inst = super().__new__(cls, val)
        inst.custom_print_str = custom_print_str
        return inst

    def __repr__(self):
        if self.custom_print_str:
            return self.custom_print_str
        return f"{self.real:,}"


# adapted from https://stackoverflow.com/a/50701137/9201239
class ScientificNotationEncoder(json.JSONEncoder):
    """
    This class overrides ``json.dumps`` default formatter.

    This version keeps everything as normal except formats numbers bigger than 1e3 using scientific notation.

    Just pass ``cls=ScientificNotationEncoder`` to ``json.dumps`` to activate it

    """

    def iterencode(self, o, _one_shot=False, level=0):
        indent = self.indent if self.indent is not None else 4
        prefix_close = " " * level * indent
        level += 1
        prefix = " " * level * indent
        if isinstance(o, bool):
            return "true" if o else "false"
        elif isinstance(o, float) or isinstance(o, int):
            if o > 1e3:
                return f"{o:e}"
            else:
                return f"{o}"
        elif isinstance(o, collections.abc.Mapping):
            x = [f'\n{prefix}"{k}": {self.iterencode(v, level=level)}' for k, v in o.items()]
            return "{" + ", ".join(x) + f"\n{prefix_close}" + "}"
        elif isinstance(o, collections.abc.Sequence) and not isinstance(o, str):
            return f"[{ f', '.join(map(self.iterencode, o)) }]"
        return "\n, ".join(super().iterencode(o, _one_shot))


class DeepSpeedConfigObject(object):
    """
    For json serialization
    """

    def repr(self):
        return self.__dict__

    def __repr__(self):
        return json.dumps(
            self.__dict__,
            sort_keys=True,
            indent=4,
            cls=ScientificNotationEncoder,
        )


def get_scalar_param(param_dict, param_name, param_default_value):
    return param_dict.get(param_name, param_default_value)


def get_list_param(param_dict, param_name, param_default_value):
    return param_dict.get(param_name, param_default_value)


def get_dict_param(param_dict, param_name, param_default_value):
    return param_dict.get(param_name, param_default_value)


def dict_raise_error_on_duplicate_keys(ordered_pairs):
    """Reject duplicate keys."""
    d = dict((k, v) for k, v in ordered_pairs)
    if len(d) != len(ordered_pairs):
        counter = collections.Counter([pair[0] for pair in ordered_pairs])
        keys = [key for key, value in counter.items() if value > 1]
        raise ValueError("Duplicate keys in DeepSpeed config: {}".format(keys))
    return d
