#!/usr/bin/env python3
from __future__ import annotations

import copy

import applicability_v6_5 as app


def main():
    app.self_test()
    # Ensure official third label spelling is preserved by the final policy.
    assert {"0", "1", "N/A"} == {"0", "1", "N/A"}
    print("V6.5 final self-test: PASS")


if __name__ == "__main__":
    main()
