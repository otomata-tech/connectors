"""Nextmotion — the service catalogue: visit types and their categories and
variants, treatment types and pricings, treatment packages, accounting
distributions (how a price is split between clinic and provider), and the
global product catalogue.

Never instantiated alone: composed into `NextmotionClient`, which provides the
transport (`_get`, `_list`).
"""
from __future__ import annotations

from typing import Any, Optional

from .._http import _id, _opt_id


class _CatalogMixin:

    # ---- visit types --------------------------------------------------------

    def list_visit_types(self, clinic_id: str, *,
                         visit_type_category_id: Optional[str] = None,
                         limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/visit_types."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/visit_types", limit, offset,
            visit_type_category=_opt_id(visit_type_category_id, "visit_type_category_id"))

    def get_visit_type(self, visit_type_id: str) -> Any:
        """GET /v4/visit_types/{visit_type_id}."""
        return self._get(f"/v4/visit_types/{_id(visit_type_id, 'visit_type_id')}")

    def list_visit_type_categories(self, clinic_id: str, *,
                                   limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/visit_type_categories."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/visit_type_categories",
            limit, offset)

    def get_visit_type_category(self, visit_type_category_id: str) -> Any:
        """GET /v4/visit_type_categories/{visit_type_category_id}."""
        return self._get("/v4/visit_type_categories/"
                         f"{_id(visit_type_category_id, 'visit_type_category_id')}")

    def list_sub_visit_types(self, clinic_id: str, *, visit_type_id: Optional[str] = None,
                             limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/sub_visit_types."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/sub_visit_types",
                          limit, offset, visit_type=_opt_id(visit_type_id, "visit_type_id"))

    def get_sub_visit_type(self, sub_visit_type_id: str) -> Any:
        """GET /v4/sub_visit_types/{sub_visit_type_id}."""
        return self._get(
            f"/v4/sub_visit_types/{_id(sub_visit_type_id, 'sub_visit_type_id')}")

    # ---- treatment types, pricings, packages --------------------------------

    def list_treatment_types(self, clinic_id: str, *, search: Optional[str] = None,
                             limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/treatment_types."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/treatment_types",
                          limit, offset, search=search)

    def get_treatment_type(self, treatment_type_id: str) -> Any:
        """GET /v4/treatment_types/{treatment_type_id}."""
        return self._get(
            f"/v4/treatment_types/{_id(treatment_type_id, 'treatment_type_id')}")

    def list_treatment_pricings(self, clinic_id: str, *,
                                treatment_type_id: Optional[str] = None,
                                limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/treatment_pricings."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/treatment_pricings", limit, offset,
            treatment_type=_opt_id(treatment_type_id, "treatment_type_id"))

    def get_treatment_pricing(self, treatment_pricing_id: str) -> Any:
        """GET /v4/treatment_pricings/{treatment_pricing_id}."""
        return self._get(
            f"/v4/treatment_pricings/{_id(treatment_pricing_id, 'treatment_pricing_id')}")

    def list_treatment_pricing_distributions(self, treatment_pricing_id: str) -> Any:
        """GET /v4/treatment_pricings/{id}/user_accounting_distributions — the
        per-user accounting distribution of a pricing. Unpaginated."""
        return self._get(
            f"/v4/treatment_pricings/{_id(treatment_pricing_id, 'treatment_pricing_id')}"
            "/user_accounting_distributions")

    def list_treatment_packages(self, clinic_id: str, *, search: Optional[str] = None,
                                limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/treatment_packages."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/treatment_packages",
            limit, offset, search=search)

    def get_treatment_package(self, treatment_package_id: str) -> Any:
        """GET /v4/treatment_packages/{treatment_package_id}."""
        return self._get(
            f"/v4/treatment_packages/{_id(treatment_package_id, 'treatment_package_id')}")

    def list_treatment_package_items(self, treatment_package_id: str, *,
                                     limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/treatment_packages/{id}/items — the treatments a package holds."""
        return self._list(
            f"/v4/treatment_packages/{_id(treatment_package_id, 'treatment_package_id')}"
            "/items", limit, offset)

    def list_treatment_package_distributions(self, treatment_package_id: str) -> Any:
        """GET /v4/treatment_packages/{id}/user_accounting_distributions.
        Unpaginated."""
        return self._get(
            f"/v4/treatment_packages/{_id(treatment_package_id, 'treatment_package_id')}"
            "/user_accounting_distributions")

    # ---- accounting distributions, global products --------------------------

    def list_accounting_distributions(self, clinic_id: str, *, limit: int = 50,
                                      offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/accounting_distributions."""
        return self._list(
            f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/accounting_distributions",
            limit, offset)

    def get_accounting_distribution(self, accounting_distribution_id: str) -> Any:
        """GET /v4/accounting_distributions/{accounting_distribution_id}."""
        return self._get(
            "/v4/accounting_distributions/"
            f"{_id(accounting_distribution_id, 'accounting_distribution_id')}")

    def list_global_products(self, clinic_id: str, *, search: Optional[str] = None,
                             limit: int = 50, offset: int = 0) -> Any:
        """GET /v4/clinics/{clinic_id}/global_products — the product catalogue
        (name, brand). No detail endpoint."""
        return self._list(f"/v4/clinics/{_id(clinic_id, 'clinic_id')}/global_products",
                          limit, offset, search=search)
