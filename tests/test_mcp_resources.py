"""Own RFC 6570 examples and peer-generation/approval boundaries."""

from __future__ import annotations

import pytest

from jarvis_core.mcp import McpError
from jarvis_core.mcp_resources import McpResourceTemplates, expand_template, template_variables


@pytest.mark.parametrize(
    ("template", "arguments", "expected"),
    [
        (
            "fixture://note/{label}",
            {"label": "azul y ámbar"},
            "fixture://note/azul%20y%20%C3%A1mbar",
        ),
        ("{+value}", {"value": "/a%20b?q=2"}, "/a%20b?q=2"),
        ("{value}", {"value": "/a%20b"}, "%2Fa%2520b"),
        ("{#value}", {"value": "part/second"}, "#part/second"),
        ("{.a,b}", {"a": "x.y", "b": "z"}, ".x.y.z"),
        ("{/a,b}", {"a": "x/y", "b": ""}, "/x%2Fy/"),
        ("{;a,b}", {"a": "", "b": "z"}, ";a;b=z"),
        ("{?a,b}", {"a": "", "b": "z z"}, "?a=&b=z%20z"),
        ("?fixed=1{&q}", {"q": "x&y"}, "?fixed=1&q=x%26y"),
        ("{label:2}", {"label": "ñ界🧪"}, "%C3%B1%E7%95%8C"),
        ("{+label:1}", {"label": "%E7%95%8Crest"}, "%E7%95%8C"),
        ("{/items*}", {"items": ["azul", "rojo rojo"]}, "/azul/rojo%20rojo"),
        ("{?items*}", {"items": ["a", "b"]}, "?items=a&items=b"),
        ("{?pairs}", {"pairs": {"a": "1", "b": "2"}}, "?pairs=a,1,b,2"),
        ("{;pairs*}", {"pairs": {"a": "", "b": "2"}}, ";a;b=2"),
        ("{?pairs*}", {"pairs": {"a": "", "b": "2"}}, "?a=&b=2"),
        ("{?absent,empty}", {"empty": []}, ""),
        ("fixture://é/{a%20b}", {"a%20b": "x"}, "fixture://%C3%A9/x"),
    ],
)
def test_template_expansion_is_exact_without_io(template, arguments, expected):
    assert expand_template(template, arguments) == expected


@pytest.mark.parametrize(
    "template",
    ["{unfinished", "{}", "{a:0}", "{a:10000}", "{a**}", "{a-b}", "prefix%xx{a}", "{a{b}}"],
)
def test_malformed_template_is_rejected_before_effect(template):
    with pytest.raises(McpError, match="resource_template"):
        template_variables(template)


def test_arguments_not_authority_and_capacity_never_silently_truncates():
    assert template_variables("file:///owner/{path}{?q,path}") == ("path", "q")
    assert expand_template("file:///{path}", {"path": "../secreto"}) == "file:///..%2Fsecreto"
    with pytest.raises(McpError, match="arguments"):
        expand_template("fixture:///{id}", {"other": "wrong"})
    with pytest.raises(McpError, match="capacity"):
        expand_template(
            "fixture:///{a}/{b}/{c}", {"a": "x" * 12000, "b": "y" * 12000, "c": "z" * 12000}
        )
    with pytest.raises(McpError, match="arguments"):
        expand_template("fixture:///{a:2}", {"a": ["one"]})


class Peer:
    connected = True
    max_pages, max_items, max_line_bytes = 3, 8, 4096

    def __init__(self):
        self.capabilities = {"resources": {}}
        self._catalog_generations = {"resources": 1}
        self.effects = []
        self.before_effect = None

    def _capability(self, feature):
        assert feature == "resources"

    async def _request(self, method, params, *, effect=False, before_dispatch=None):
        if method == "resources/templates/list":
            return {
                "resourceTemplates": [{"name": "own", "uriTemplate": "fixture://note/{id}{?q}"}]
            }
        assert method == "resources/read" and effect
        if self.before_effect:
            self.before_effect()
        before_dispatch()
        self.effects.append(params)
        return {"contents": [{"uri": params["uri"], "text": "own external marker"}]}


async def test_bound_template_read_checks_catalog_and_authority_before_peer_effect():
    peer, approved = Peer(), []
    manager = McpResourceTemplates(peer)
    listed = await manager.list_templates()
    assert listed[0]["variables"] == ("id", "q")
    assert manager.expand("fixture://note/{id}{?q}", {"id": "first"}) == "fixture://note/first"
    with pytest.raises(McpError, match="approval_required"):
        await manager.read("fixture://note/{id}{?q}", {"id": "first"})

    def grant():
        approved.append("exact")

    result = await manager.read("fixture://note/{id}{?q}", {"id": "first"}, dispatch_guard=grant)
    assert result["contents"][0]["uri"] == "fixture://note/first"
    assert peer.effects == [{"uri": "fixture://note/first"}] and approved == ["exact"]
    peer.before_effect = lambda: peer._catalog_generations.update(resources=2)
    with pytest.raises(McpError, match="catalog_changed"):
        await manager.read("fixture://note/{id}{?q}", {"id": "second"}, dispatch_guard=grant)
    assert len(peer.effects) == 1 and approved == ["exact"]
