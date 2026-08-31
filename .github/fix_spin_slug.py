from pathlib import Path

p = Path('src/treepolo_mlb_data/supplemental_data.py')
text = p.read_text(encoding='utf-8')
old = '''    def spin_aggregate(self, pitcher: int) -> requests.Response:
        return self.get(f"{BASE}/savant-player/{pitcher}", params={"playerType": "pitcher"})
'''
new = '''    def spin_aggregate(self, pitcher: int) -> requests.Response:
        # Savant's numeric-only player route can silently render the batting
        # variant even when playerType=pitcher is requested.  Resolve the
        # canonical slug from that page, then fetch the slugged pitching page,
        # which is the page that actually embeds serverVals.spinAxis.
        first = self.get(f"{BASE}/savant-player/{pitcher}", params={"playerType": "pitcher"})
        first_text = first.content.decode("utf-8", errors="replace")
        if "image_spin_x" in first_text and re.search(r"\\bspinAxis\\s*:\\s*\\[", first_text):
            return first
        match = re.search(r"\\bslug\\s*:\\s*['\\\"]([^'\\\"]+)['\\\"]", first_text)
        if not match:
            raise ValueError(f"Could not resolve Savant player slug for pitcher {pitcher}")
        slug = match.group(1)
        return self.get(f"{BASE}/savant-player/{slug}", params={"playerType": "pitcher"})
'''
if old not in text:
    raise SystemExit('spin_aggregate method marker missing')
p.write_text(text.replace(old, new, 1), encoding='utf-8')

# Add a regression for the numeric-route-to-slugged-pitcher-page resolution.
t = Path('tests/test_supplemental_data.py')
source = t.read_text(encoding='utf-8')
append = r'''

def test_spin_aggregate_resolves_slugged_pitcher_page(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    client = SupplementalClient(cfg)
    numeric = FakeResponse(
        b"<html><script>var serverVals={slug: 'shohei-ohtani-660271', spinAxis: {}};</script></html>",
        "text/html; charset=utf-8",
    )
    pitching = FakeResponse(
        b'<html><script>var serverVals={spinAxis: [{"image_spin_x":0.1}]};</script></html>',
        "text/html; charset=utf-8",
    )
    calls = []

    def fake_get(url, *, params=None):
        calls.append((url, params))
        return numeric if url.endswith('/savant-player/660271') else pitching

    monkeypatch.setattr(client, 'get', fake_get)
    response = client.spin_aggregate(660271)
    assert response is pitching
    assert calls == [
        ('https://baseballsavant.mlb.com/savant-player/660271', {'playerType': 'pitcher'}),
        ('https://baseballsavant.mlb.com/savant-player/shohei-ohtani-660271', {'playerType': 'pitcher'}),
    ]
'''
if 'def test_spin_aggregate_resolves_slugged_pitcher_page' not in source:
    t.write_text(source + append, encoding='utf-8')
