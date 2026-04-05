from bitget_bot import runner


def test_make_exchange_strips_placeholder_credentials(monkeypatch):
    monkeypatch.setenv("BITGET_API_KEY", "your_api_key_here")
    monkeypatch.setenv("BITGET_API_SECRET", "your_api_secret_here")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "your_passphrase_here")

    exchange = runner.make_exchange()

    assert exchange.apiKey == ""
    assert exchange.secret == ""
    assert exchange.password == ""


def test_signed_contracts_skips_private_query_in_dry_run_without_real_credentials():
    class _Exchange:
        apiKey = ""
        secret = ""

        def fetch_positions(self, symbols):
            raise AssertionError("fetch_positions should not be called")

    result = runner.signed_contracts(_Exchange(), "BTC/USDT:USDT", dry_run=True)

    assert result == 0.0
