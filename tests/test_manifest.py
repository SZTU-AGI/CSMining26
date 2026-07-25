from pathlib import Path

from freca.manifest import build_manifest, recover_case_id


def test_recovers_case_id_from_numbered_track_names() -> None:
    assert recover_case_id("2_HACCPPlan_24_Mid_North.docx") == 24
    assert recover_case_id("3_PestControlRecord_100_Midwest.xlsx") == 100
    assert recover_case_id("8_Phytosanitary_Procedure_Farm_035.docx") == 35
    assert recover_case_id("9_Traceability_Farm_100.xlsx") == 100
    assert recover_case_id("4_Farm-Management-Plan_Farm.docx") is None


def test_build_manifest_splits_a_mixed_directory(tmp_path: Path) -> None:
    mixed = tmp_path / "RE-WA-2021-0077"
    mixed.mkdir()
    filenames = [
        "2_HACCPPlan_35_Goldfields.docx",
        "4_Farm-Management-Plan_Goldfields.docx",
        "8_Phytosanitary_Goldfields_035.docx",
        "2_HACCPPlan_100_Midwest.docx",
        "4_Farm-Management-Plan_Midwest.docx",
        "8_Phytosanitary_Midwest_100.docx",
    ]
    for filename in filenames:
        (mixed / filename).write_bytes(filename.encode("utf-8"))

    manifest = build_manifest(tmp_path, expected_case_ids={35, 100})

    assert len(manifest.cases) == 2
    assert manifest.by_id(35).re_number == manifest.by_id(100).re_number
    assert {source.path.name for source in manifest.by_id(35).sources} == {
        "2_HACCPPlan_35_Goldfields.docx",
        "4_Farm-Management-Plan_Goldfields.docx",
        "8_Phytosanitary_Goldfields_035.docx",
    }
    assert set(manifest.by_id(35).source_paths).isdisjoint(
        manifest.by_id(100).source_paths
    )


def test_real_manifest_has_100_cases_and_898_sources() -> None:
    root = Path(__file__).parents[1] / "extracted" / "SFRE_cases"
    manifest = build_manifest(root)

    assert len(manifest.cases) == 100
    assert sum(len(case.sources) for case in manifest.cases) == 898
    assert manifest.by_id(24).missing_tracks == [1]
    assert manifest.by_id(80).missing_tracks == [1]
    assert manifest.by_id(35).re_number == manifest.by_id(100).re_number
    assert set(manifest.by_id(35).source_paths).isdisjoint(
        manifest.by_id(100).source_paths
    )
