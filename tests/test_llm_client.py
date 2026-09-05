class TestAnthropicProvider:
    """Claude를 붙였다 (D91).

    **어댑터를 안 만들었다.** Anthropic이 OpenAI 호환 `/v1/chat/completions`를
    받아 주고, 우리가 보내는 것은 `model`·`messages`·토큰 상한 셋뿐이라
    그 계층이 무시하는 필드를 하나도 안 쓴다.
    """

    def test_it_is_registered(self):
        from arc.llm.client import PROVIDERS

        assert "anthropic" in PROVIDERS

    def test_it_speaks_the_openai_shape(self):
        """base_url 뒤에 `/chat/completions`가 붙는다 — 공통 클라이언트가
        그렇게 부른다. 슬래시가 겹치거나 빠지면 404다."""
        from arc.llm.client import PROVIDERS

        spec = PROVIDERS["anthropic"]
        assert spec.base_url == "https://api.anthropic.com/v1"
        assert not spec.base_url.endswith("/")

    def test_we_send_nothing_the_compat_layer_ignores(self):
        """**이게 어댑터가 필요 없는 이유다.**

        호환 계층은 `response_format`·`seed`·`logprobs`·`presence_penalty`를
        조용히 무시한다. 우리가 그중 하나라도 보내면 openai에서는 되고
        anthropic에서는 조용히 다르게 동작한다 — 그 종류의 차이가 제일 나쁘다.
        """
        import inspect

        from arc.llm.client import OpenAICompatClient

        src = inspect.getsource(OpenAICompatClient.complete)
        body = src[src.index("payload = {") : src.index("t0 = ")]
        for ignored in ("response_format", "seed", "logprobs", "presence_penalty", "temperature"):
            assert ignored not in body, f"{ignored}를 보내면 provider 간에 조용히 갈린다"

    def test_the_write_tier_is_priced_where_the_decision_was_made(self):
        """월 $30이라는 판단이 이 단가에서 나왔다 — 바뀌면 판단도 다시 해야 한다."""
        from arc.llm.client import PROVIDERS, Tier

        p = PROVIDERS["anthropic"].pricing[Tier.WRITE]
        assert (p.input, p.output) == (5.00, 25.00)

    def test_a_missing_key_names_the_variable(self, monkeypatch):
        """**어느 키가 없는지 말한다.** 「키가 없습니다」만으로는 넷 중 뭘
        넣어야 하는지 모른다."""
        from arc.llm.client import get_client

        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        try:
            get_client("anthropic")
        except ValueError as exc:
            assert "ANTHROPIC_API_KEY" in str(exc)
        else:
            raise AssertionError("키가 없는데 통과했다")

    def test_an_unknown_provider_lists_the_known_ones(self):
        from arc.llm.client import get_client

        try:
            get_client("claude")  # 흔한 오타 — 이름은 anthropic 이다
        except ValueError as exc:
            assert "anthropic" in str(exc)
        else:
            raise AssertionError("모르는 이름이 통과했다")
