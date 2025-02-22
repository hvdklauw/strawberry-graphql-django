import functools
import inspect
from enum import Enum
from types import FunctionType
from typing import Generic, List, Optional, Type, TypeVar

import strawberry
from django.db.models import QuerySet
from django.db.models.sql.query import get_field_names_from_opts
from strawberry import UNSET
from strawberry.arguments import StrawberryArgument
from strawberry.types import Info

from . import utils
from .arguments import argument


T = TypeVar("T")


@strawberry.input
class DjangoModelFilterInput:
    pk: strawberry.ID


@strawberry.input
class FilterLookup(Generic[T]):
    exact: Optional[T] = UNSET
    i_exact: Optional[T] = UNSET
    contains: Optional[T] = UNSET
    i_contains: Optional[T] = UNSET
    in_list: Optional[List[T]] = UNSET
    gt: Optional[T] = UNSET
    gte: Optional[T] = UNSET
    lt: Optional[T] = UNSET
    lte: Optional[T] = UNSET
    starts_with: Optional[T] = UNSET
    i_starts_with: Optional[T] = UNSET
    ends_with: Optional[T] = UNSET
    i_ends_with: Optional[T] = UNSET
    range: Optional[List[T]] = UNSET
    is_null: Optional[bool] = UNSET
    regex: Optional[str] = UNSET
    i_regex: Optional[str] = UNSET


lookup_name_conversion_map = {
    "i_exact": "iexact",
    "i_contains": "icontains",
    "in_list": "in",
    "starts_with": "startswith",
    "i_starts_with": "istartswith",
    "ends_with": "endswith",
    "i_ends_with": "iendswith",
    "is_null": "isnull",
    "i_regex": "iregex",
}


def filter(model, *, name=None, lookups=False):
    def wrapper(cls):
        is_filter = lookups and "lookups" or True
        from .type import process_type

        type_ = process_type(
            cls, model, is_input=True, partial=True, is_filter=is_filter
        )
        return type_

    return wrapper


def filter_deprecated(model, *, name=None, lookups=False):
    utils.deprecated(
        (
            "'strawberry_django.filter' is deprecated,"
            " use 'strawberry_django.filters.filter' instead"
        ),
        stacklevel=2,
    )
    return filter(model, name=name, lookups=lookups)


def build_filter_kwargs(filters):
    filter_kwargs = {}
    filter_methods = []
    django_model = utils.get_django_model(filters)
    for field in utils.fields(filters):
        field_name = field.name
        field_value = getattr(filters, field_name)

        if field_value is UNSET:
            continue

        if isinstance(field_value, Enum):
            field_value = field_value.value

        filter_method = getattr(filters, f"filter_{field_name}", None)
        if filter_method:
            filter_methods.append(filter_method)
            continue

        if django_model:
            if field_name not in get_field_names_from_opts(django_model._meta):
                continue

        if field_name in lookup_name_conversion_map:
            field_name = lookup_name_conversion_map[field_name]
        if utils.is_strawberry_type(field_value):
            (
                subfield_filter_kwargs,
                subfield_filter_methods,
            ) = build_filter_kwargs(field_value)
            for (
                subfield_name,
                subfield_value,
            ) in subfield_filter_kwargs.items():
                if isinstance(subfield_value, Enum):
                    subfield_value = subfield_value.value
                filter_kwargs[f"{field_name}__{subfield_name}"] = subfield_value
            filter_methods.extend(subfield_filter_methods)
        else:
            filter_kwargs[field_name] = field_value

    return filter_kwargs, filter_methods


@functools.lru_cache(maxsize=256)
def function_allow_passing_info(filter_method: FunctionType) -> bool:
    argspec = inspect.getfullargspec(filter_method)

    return "info" in getattr(argspec, "args", []) or "info" in getattr(
        argspec, "kwargs", []
    )


def apply(filters, queryset: QuerySet, info=UNSET, pk=UNSET) -> QuerySet:
    if pk is not UNSET:
        queryset = queryset.filter(pk=pk)

    if (
        filters is UNSET
        or filters is None
        or not hasattr(filters, "_django_type")
        or not filters._django_type.is_filter
    ):
        return queryset

    filter_method = getattr(filters, "filter", None)
    if filter_method:
        if function_allow_passing_info(
            # Pass the original __func__ which is always the same
            getattr(filter_method, "__func__", filter_method)
        ):
            return filter_method(queryset=queryset, info=info)

        else:
            return filter_method(queryset=queryset)

    filter_kwargs, filter_methods = build_filter_kwargs(filters)
    queryset = queryset.filter(**filter_kwargs)
    for filter_method in filter_methods:
        if function_allow_passing_info(
            # Pass the original __func__ which is always the same
            getattr(filter_method, "__func__", filter_method)
        ):
            queryset = filter_method(queryset=queryset, info=info)

        else:
            queryset = filter_method(queryset=queryset)

    return queryset


class StrawberryDjangoFieldFilters:
    def __init__(self, filters=UNSET, **kwargs):
        self.filters = filters
        super().__init__(**kwargs)

    @property
    def arguments(self) -> List[StrawberryArgument]:
        arguments = []
        if not self.base_resolver:
            filters = self.get_filters()
            if (
                self.django_model
                and not self.is_list
                and self.origin._type_definition.name == "Query"
            ):
                arguments.append(argument("pk", strawberry.ID, is_optional=False))
            elif self.django_model and not self.is_list:
                # Do not add filters to non list fields
                pass
            elif filters and filters is not UNSET:
                arguments.append(argument("filters", filters))
        return super().arguments + arguments

    def get_filters(self) -> Optional[Type]:
        if self.filters is not UNSET:
            return self.filters
        type_ = utils.unwrap_type(self.type or self.child.type)

        if utils.is_django_type(type_):
            return type_._django_type.filters
        return None

    def apply_filters(
        self, queryset: QuerySet, filters: Type = UNSET, pk=UNSET, info: Info = UNSET
    ) -> QuerySet:
        return apply(filters, queryset, info, pk)

    def get_queryset(
        self, queryset: QuerySet, info: Info, pk=UNSET, filters: Type = UNSET, **kwargs
    ):
        queryset = super().get_queryset(queryset, info, **kwargs)
        return self.apply_filters(queryset, filters, pk, info)
