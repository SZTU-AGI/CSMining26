from pathlib import Path

from freca.evaluation import load_gold_labels


def test_load_gold_labels_includes_only_confirmed_verdicts() -> None:
    labels = load_gold_labels(Path("gold/consensus-v1.json"))

    assert len(labels) == 34
    assert labels[(23, "CP1")].verdict == "0"
    assert labels[(65, "CP12")].verdict == "1"
    assert (23, "CP24") not in labels
    assert (35, "CP35") not in labels
    assert (23, "CP17") not in labels
    assert (23, "CP19") not in labels
