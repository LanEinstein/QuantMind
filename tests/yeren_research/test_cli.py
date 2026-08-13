from scripts.yeren_research.__main__ import _parser


def _bundle_args(*endpoints: str) -> list[str]:
    args = [
        "bundle-market",
        "--case-id",
        "case",
        "--video-id",
        "video",
        "--decision-cutoff",
        "2026-07-04T10:58:00+08:00",
        "--start-date",
        "20260701",
        "--end-date",
        "20260710",
    ]
    for endpoint in endpoints:
        args.extend(("--endpoint", endpoint))
    return args


def test_market_cli_does_not_prepend_default_to_explicit_endpoints() -> None:
    args = _parser().parse_args(_bundle_args("daily", "daily_basic"))

    assert args.endpoint == ["daily", "daily_basic"]


def test_market_cli_leaves_default_selection_to_execution() -> None:
    args = _parser().parse_args(_bundle_args())

    assert args.endpoint is None
