"""Listing validation and the 1080x1080 output requirement.

These two are tested together because they are the pair of rules that decide whether
a package may leave inbox/: the facts must match the live listing, and the graphic
must be exactly 1080x1080.
"""
from __future__ import annotations

import pytest
from PIL import Image

from ims_ads import packaging
from ims_ads.copywriting import build_from_template, check_description
from ims_ads.core import ValidationReport
from ims_ads.media import (ImageError, choose_exact_unit_image, image_belongs_to_unit,
                           listing_id, looks_generated_or_placeholder)
from ims_ads.validation import (validate_freshness, validate_media, validate_package,
                                validate_unit_fields)

from .conftest import make_unit

CDN = "https://cdnmedia.endeavorsuite.com/images/organizations/00c07879/inventory"


def build_complete_package(workspace, config, unit=None, *, size=(1080, 1080),
                           description=None, state="inbox"):
    """A package that passes everything, so each test can break exactly one thing."""
    unit = unit or make_unit()
    pkg = packaging.create_package(unit, state=state)
    Image.new("RGB", size, "white").save(pkg / packaging.GRAPHIC_FILE)
    Image.new("RGB", (1600, 1200), "white").save(pkg / "source-image.jpg")
    text = description if description is not None else build_from_template(unit, config)
    (pkg / packaging.DESCRIPTION_FILE).write_text(text, encoding="utf-8")
    return pkg, unit


# ------------------------------------------------------- 1080x1080 requirement

def test_exact_1080_square_passes(workspace, config):
    pkg, _ = build_complete_package(workspace, config, size=(1080, 1080))
    report, _ = validate_package(pkg, config, check_duplicates=False)
    sizes = [c for c in report.checks if c.name == "graphic.is_1080x1080"]
    assert sizes and sizes[0].passed


@pytest.mark.parametrize("size", [(1200, 1200), (1080, 1350), (1079, 1080),
                                  (1081, 1080), (540, 540)])
def test_any_other_size_fails(workspace, config, size):
    pkg, _ = build_complete_package(workspace, config, size=size)
    report, _ = validate_package(pkg, config, check_duplicates=False)
    assert not report.ok
    assert any(c.name == "graphic.is_1080x1080" and not c.passed
               for c in report.checks), f"{size} should have been rejected"


def test_missing_graphic_fails(workspace, config):
    pkg, _ = build_complete_package(workspace, config)
    (pkg / packaging.GRAPHIC_FILE).unlink()
    report, _ = validate_package(pkg, config, check_duplicates=False)
    assert not report.ok
    assert any(c.name == "graphic.present" and not c.passed for c in report.checks)


def test_unreadable_graphic_fails(workspace, config):
    pkg, _ = build_complete_package(workspace, config)
    (pkg / packaging.GRAPHIC_FILE).write_bytes(b"this is not a png")
    report, _ = validate_package(pkg, config, check_duplicates=False)
    assert not report.ok


# ------------------------------------------------------------ listing validation

def test_complete_unit_passes_field_checks(workspace, config, unit):
    report = ValidationReport()
    validate_unit_fields(unit, config, report)
    assert report.ok, [str(c) for c in report.failures]


@pytest.mark.parametrize("field", ["year", "make", "model", "inventory_url"])
def test_missing_required_field_fails(workspace, config, field):
    report = ValidationReport()
    validate_unit_fields(make_unit(**{field: ""}), config, report)
    assert not report.ok
    assert any(field in c.name for c in report.failures)


def test_missing_trim_and_colour_are_warnings_not_blockers(workspace, config):
    report = ValidationReport()
    validate_unit_fields(make_unit(trim="", color=""), config, report)
    assert report.ok, "powersports listings often omit these; they must not block"
    assert len(report.warnings) >= 2


def test_missing_price_fails(workspace, config):
    report = ValidationReport()
    validate_unit_fields(make_unit(sale_price=None), config, report)
    assert not report.ok


def test_missing_stock_and_vin_fails(workspace, config):
    report = ValidationReport()
    validate_unit_fields(make_unit(stock_number="", vin=""), config, report)
    assert not report.ok


def test_savings_below_threshold_fails(workspace, config):
    report = ValidationReport()
    validate_unit_fields(make_unit(savings=100.0, regular_price=20098.0),
                         config, report)
    assert not report.ok


def test_inconsistent_price_arithmetic_fails(workspace, config):
    """regular - sale must equal savings, or one of the three is wrong."""
    report = ValidationReport()
    validate_unit_fields(
        make_unit(sale_price=19998.0, regular_price=34998.0, savings=9000.0),
        config, report)
    assert any(c.name == "unit.price_arithmetic" and not c.passed
               for c in report.checks)


def test_excluded_keyword_in_title_fails(workspace, config):
    report = ValidationReport()
    validate_unit_fields(make_unit(title_raw="2024 LXP MV Agusta SOLD"), config, report)
    assert not report.ok


def test_stale_listing_fails(workspace, config):
    report = ValidationReport()
    validate_freshness(make_unit(scraped_at="2020-01-01T00:00:00+00:00"),
                       config, report)
    assert not report.ok


def test_fresh_listing_passes(workspace, config, unit):
    report = ValidationReport()
    validate_freshness(unit, config, report)
    assert report.ok


# -------------------------------------------------------- exact-unit image rules

def test_listing_id_is_extracted_from_the_url(unit):
    assert listing_id(unit) == "12266444"


def test_image_under_the_units_listing_id_is_accepted(unit):
    assert image_belongs_to_unit(f"{CDN}/12266444/photo-first.jpg", unit)


def test_image_from_a_different_unit_is_rejected(unit):
    assert not image_belongs_to_unit(f"{CDN}/99999999/photo-first.jpg", unit)


def test_image_from_another_host_is_rejected(unit):
    assert not image_belongs_to_unit("https://images.google.com/bike.jpg", unit)


@pytest.mark.parametrize("name", ["ChatGPTImageSep32026.png", "placeholder.jpg",
                                  "coming-soon.png", "stock-photo-1.jpg"])
def test_generated_and_placeholder_filenames_are_rejected(config, name):
    patterns = config["image"]["reject_filename_patterns"]
    assert looks_generated_or_placeholder(f"{CDN}/12266444/{name}", patterns)


def test_real_photo_filename_is_accepted(config):
    patterns = config["image"]["reject_filename_patterns"]
    assert not looks_generated_or_placeholder(
        f"{CDN}/12266444/2024-mv-agusta-lxp-orioli-first.jpg", patterns)


def test_no_verifiable_photo_raises_rather_than_substituting(config):
    """The critical rule: never fall back to a different motorcycle."""
    unit = make_unit(image_urls=[f"{CDN}/99999999/some-other-bike.jpg",
                                 "https://images.example.com/generic-motorcycle.jpg"])
    with pytest.raises(ImageError, match="exact unit"):
        choose_exact_unit_image(unit, config)


def test_unit_with_no_images_at_all_raises(config):
    with pytest.raises(ImageError):
        choose_exact_unit_image(make_unit(image_urls=[]), config)


def test_primary_photo_is_preferred(config):
    unit = make_unit(image_urls=[f"{CDN}/12266444/detail-shot-3.jpg",
                                 f"{CDN}/12266444/lxp-orioli-first.jpg"])
    chosen, _ = choose_exact_unit_image(unit, config)
    assert chosen.endswith("lxp-orioli-first.jpg")


# ------------------------------------------------------- description agreement

def test_template_description_passes_its_own_checks(config, unit):
    failures, _ = check_description(build_from_template(unit, config), unit, config)
    assert failures == []


def test_description_naming_a_different_unit_fails(config, unit):
    other = build_from_template(make_unit(year="2019", make="Honda", model="Rebel"),
                                config)
    failures, _ = check_description(other, unit, config)
    assert failures


def test_description_missing_phone_fails(config, unit):
    text = build_from_template(unit, config).replace("778-653-5702", "")
    failures, _ = check_description(text, unit, config)
    assert any("phone" in f for f in failures)


def test_description_missing_inventory_url_fails(config, unit):
    text = build_from_template(unit, config).replace(unit.inventory_url, "")
    failures, _ = check_description(text, unit, config)
    assert any("inventory URL" in f for f in failures)


def test_description_with_stale_price_fails(config, unit):
    text = build_from_template(unit, config).replace("$19,998", "$12,345")
    failures, _ = check_description(text, unit, config)
    assert any("sale price" in f for f in failures)


def test_description_with_banned_stock_claim_fails(config, unit):
    text = build_from_template(unit, config) + "\nOnly 2 units available!\n"
    failures, _ = check_description(text, unit, config)
    assert any("banned phrase" in f for f in failures)


def test_empty_description_fails(config, unit):
    failures, _ = check_description("", unit, config)
    assert failures


# ----------------------------------------------------------- end-to-end gating

def test_fully_valid_package_passes(workspace, config):
    pkg, _ = build_complete_package(workspace, config)
    report, _ = validate_package(pkg, config, check_duplicates=False)
    assert report.ok, report.render()


def test_one_broken_rule_blocks_the_whole_package(workspace, config):
    pkg, _ = build_complete_package(workspace, config, size=(1080, 1081))
    report, _ = validate_package(pkg, config, check_duplicates=False)
    assert not report.ok
    assert len(report.failures) >= 1


# ------------------------------------------------- composer output is 1080x1080

@pytest.fixture
def brand_logo(tmp_path):
    """Stand-in for assets/brand/logo.png so the composer can be exercised."""
    path = tmp_path / "logo.png"
    Image.new("RGBA", (1000, 260), (200, 16, 46, 255)).save(path)
    return path


@pytest.mark.parametrize("source_size", [(1620, 1080), (800, 600), (2400, 2400),
                                         (1080, 1920)])
def test_composed_graphic_is_always_exactly_1080x1080(workspace, config, unit,
                                                      brand_logo, tmp_path,
                                                      source_size):
    """Whatever shape the unit photo is, the output must be a 1080 square."""
    from ims_ads.media import compose_graphic

    config = dict(config)
    config["graphic"] = dict(config["graphic"], logo_path=str(brand_logo))

    source = tmp_path / "src.jpg"
    Image.new("RGB", source_size, "grey").save(source)
    dest = tmp_path / "out.png"

    compose_graphic(unit, source, dest, config)
    with Image.open(dest) as img:
        assert img.size == (1080, 1080)


def test_composer_refuses_without_branding_rather_than_faking_it(workspace, config,
                                                                 unit, tmp_path):
    """No logo must mean a clear error, never an unbranded or invented graphic."""
    from ims_ads.media import compose_graphic

    config = dict(config)
    config["graphic"] = dict(config["graphic"],
                             logo_path=str(tmp_path / "does-not-exist.png"))
    source = tmp_path / "src.jpg"
    Image.new("RGB", (1620, 1080), "grey").save(source)

    with pytest.raises(ImageError, match="Branding asset missing"):
        compose_graphic(unit, source, tmp_path / "out.png", config)
