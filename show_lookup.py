#!/usr/bin/env python3
"""Identify a TV disc and map its MakeMKV titles to numbered episodes.

Called by rip-shows.sh. Every command prints tab-separated lines so the shell
can read them with a plain `while read` loop.

Episode data comes from TVmaze, which needs no API key. TVmaze also publishes
DVD running orders for shows that shipped out of broadcast order (Firefly being
the usual example), which is what `--order dvd` uses.
"""

import argparse
import json
import re
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request

TVMAZE_ROOT = "https://api.tvmaze.com"
REQUEST_TIMEOUT_SECONDS = 20

# A disc title is treated as an episode when its length lands inside this
# fraction of the season's typical episode. The top end is deliberately loose:
# a feature-length premiere can run three times a normal episode, and TVmaze
# reports broadcast slots, so the "typical episode" it implies is only a guess.
# Bundles are caught by their own test below rather than by this ceiling.
SHORTEST_EPISODE_FRACTION = 0.55
LONGEST_EPISODE_FRACTION = 3.0



class TvMazeClient:
    """Fetches shows and episode lists from TVmaze."""

    def _get_json(self, path, query=None):
        url = f"{TVMAZE_ROOT}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": "media-server/rip-shows"})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def search_shows(self, query):
        results = self._get_json("/search/shows", {"q": query}) or []
        return [self._describe_show(result["show"]) for result in results]

    def _describe_show(self, show):
        network = show.get("network") or show.get("webChannel") or {}
        return {
            "id": show["id"],
            "name": show["name"],
            "year": (show.get("premiered") or "")[:4],
            "network": network.get("name") or "",
            "average_runtime": show.get("averageRuntime") or show.get("runtime") or 0,
        }

    def has_dvd_order(self, show_id):
        return self._dvd_list_id(show_id) is not None

    def _dvd_list_id(self, show_id):
        for alternate_list in self._get_json(f"/shows/{show_id}/alternatelists") or []:
            if alternate_list.get("dvd_release"):
                return alternate_list["id"]
        return None

    def episodes(self, show_id, order):
        """Every episode for the show as dicts of season, number, name, runtime."""
        if order == "dvd":
            dvd_list_id = self._dvd_list_id(show_id)
            if dvd_list_id is None:
                return []
            raw_episodes = self._get_json(f"/alternatelists/{dvd_list_id}/alternateepisodes") or []
        else:
            raw_episodes = self._get_json(f"/shows/{show_id}/episodes") or []

        episodes = []
        for raw_episode in raw_episodes:
            if raw_episode.get("season") is None or raw_episode.get("number") is None:
                continue
            episodes.append(
                {
                    "season": raw_episode["season"],
                    "number": raw_episode["number"],
                    "name": raw_episode.get("name") or "",
                    "runtime": raw_episode.get("runtime") or 0,
                }
            )
        return episodes


class DiscTitles:
    """The titles MakeMKV found, narrowed down to the ones that look like episodes.

    A TV disc carries more than its episodes: menus, extras, and a "Play All"
    that concatenates the lot. Blu-rays add duplicate playlists on top. This
    class throws those away and keeps what is left in disc order.
    """

    def __init__(self, titles):
        self.titles = titles

    @classmethod
    def from_tsv(cls, stream):
        """Read the table rip-shows.sh builds from `makemkvcon info`."""
        titles = []
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 4 or not fields[0].isdigit():
                continue
            titles.append(
                {
                    "title_id": int(fields[0]),
                    "seconds": int(fields[1]),
                    "duration": fields[2],
                    "bytes": int(fields[3]) if fields[3].isdigit() else 0,
                }
            )
        return cls(titles)

    def episode_titles(self, expected_episode_seconds, shortest_allowed_seconds):
        """Titles that plausibly hold one episode, in disc order."""
        candidates = self._without_duplicate_playlists()
        if expected_episode_seconds:
            candidates = self._within_episode_length(candidates, expected_episode_seconds)
        candidates = [title for title in candidates if title["seconds"] >= shortest_allowed_seconds]
        return self._without_play_all(candidates)

    def _without_duplicate_playlists(self):
        """Drop repeats of the same playlist.

        Blu-rays often expose one episode through several playlists. Matching on
        length alone would throw away genuine episodes that happen to run the
        same time, so a duplicate has to match on byte size too.
        """
        seen_playlists = set()
        unique_titles = []
        for title in self.titles:
            fingerprint = (title["seconds"], title["bytes"])
            if fingerprint in seen_playlists:
                continue
            seen_playlists.add(fingerprint)
            unique_titles.append(title)
        return unique_titles

    def _within_episode_length(self, titles, expected_episode_seconds):
        shortest = expected_episode_seconds * SHORTEST_EPISODE_FRACTION
        longest = expected_episode_seconds * LONGEST_EPISODE_FRACTION
        return [title for title in titles if shortest <= title["seconds"] <= longest]

    # A "Play All" is every episode joined together, so it runs about as long as
    # all the other titles added up. Comparing against that sum separates it from
    # a feature-length premiere, which is merely long. A simple length ceiling
    # cannot: Firefly's 122-minute pilot outruns a two-episode bundle.
    PLAY_ALL_TOLERANCE = 0.12

    def _without_play_all(self, titles):
        if len(titles) < 3:
            return titles

        total_seconds = sum(title["seconds"] for title in titles)
        kept_titles = []
        for title in titles:
            other_titles_seconds = total_seconds - title["seconds"]
            if not other_titles_seconds:
                kept_titles.append(title)
                continue
            ratio = title["seconds"] / other_titles_seconds
            if abs(ratio - 1.0) <= self.PLAY_ALL_TOLERANCE:
                continue
            kept_titles.append(title)
        return kept_titles


class EpisodeAlignment:
    """Works out which episodes of a season sit on this disc.

    Discs rarely say "this is disc 3". What they do carry is a shape: how many
    episodes, and whether any of them run long or short. A double-length
    premiere makes that shape distinctive, so comparing it against the season's
    episode list usually pins down where the disc starts.

    Each length is sorted into short, normal, or double relative to its own
    list's median, and the two sequences of labels are compared. Minutes are
    never compared directly: TVmaze reports broadcast slots, so a 44-minute
    episode is listed as 60 and absolute times would never line up.
    """

    SHORT_EPISODE_CUTOFF = 0.7
    DOUBLE_EPISODE_CUTOFF = 1.5

    def __init__(self, season_episodes, disc_titles):
        self.season_episodes = season_episodes
        self.disc_titles = disc_titles

    def best_start(self, preferred_start=None):
        """Return (start_index, is_confident, note)."""
        if not self.disc_titles or not self.season_episodes:
            return 0, False, "nothing to match"

        last_possible_start = len(self.season_episodes) - len(self.disc_titles)
        if last_possible_start < 0:
            return 0, False, (
                f"disc has {len(self.disc_titles)} titles but the season only lists "
                f"{len(self.season_episodes)} episodes"
            )
        if last_possible_start == 0:
            return 0, True, "the disc holds the whole season"

        scored_starts = sorted(
            (self._mismatch_score(start), start) for start in range(last_possible_start + 1)
        )
        best_score, best_start = scored_starts[0]
        starts_tied_with_best = [start for score, start in scored_starts if score == best_score]

        if preferred_start is not None:
            if preferred_start > last_possible_start:
                return best_start, False, (
                    f"episode {preferred_start + 1} leaves no room for {len(self.disc_titles)} "
                    f"titles in a {len(self.season_episodes)}-episode season, so it was ignored"
                )
            if preferred_start in starts_tied_with_best:
                return preferred_start, True, "the episode lengths agree with this starting point"
            return preferred_start, False, (
                f"the episode lengths look more like this disc starts at episode {best_start + 1}"
            )

        if best_score == 0 and len(starts_tied_with_best) == 1:
            return best_start, True, "a long or short episode pins the disc to this spot"
        return best_start, False, "every episode runs about the same length, so lengths cannot place this disc"

    def _mismatch_score(self, start):
        """Fraction of disc titles whose length class differs from the episode's."""
        episode_classes = self._length_classes(
            [episode["runtime"] for episode in self.season_episodes]
        )[start:start + len(self.disc_titles)]
        disc_classes = self._length_classes([title["seconds"] for title in self.disc_titles])
        if not episode_classes or not disc_classes:
            return 1.0
        mismatches = sum(
            1 for episode_class, disc_class in zip(episode_classes, disc_classes)
            if episode_class != disc_class
        )
        return mismatches / len(disc_classes)

    def _length_classes(self, lengths):
        """Label each length short, normal, or double against the list's median."""
        usable_lengths = [length for length in lengths if length]
        if not usable_lengths:
            return []
        middle = statistics.median(usable_lengths)
        if not middle:
            return []

        classes = []
        for length in lengths:
            if not length:
                classes.append("unknown")
                continue
            if length >= middle * self.DOUBLE_EPISODE_CUTOFF:
                classes.append("double")
                continue
            if length <= middle * self.SHORT_EPISODE_CUTOFF:
                classes.append("short")
                continue
            classes.append("normal")
        return classes


class DiscKind:
    """Decides whether a disc holds a film or episodes of a show.

    The tell is shape, not length. A film disc has one dominant title with
    assorted shorter extras. An episode disc has several titles of near-identical
    length, because episodes of a season run to the same slot.

    Signals are weighed rather than applied in order, since any one of them can
    be wrong: Firefly's first disc opens with a feature-length pilot that looks
    exactly like a film sitting next to two episodes.
    """

    SHORTEST_EPISODE_SECONDS = 900
    LONGEST_EPISODE_SECONDS = 4500
    SIMILAR_LENGTH_TOLERANCE = 0.12

    def __init__(self, titles, label=""):
        self.titles = titles
        self.label = label

    def verdict(self):
        """Return (kind, is_confident, reason)."""
        show_score, show_reason = self._show_evidence()
        film_score, film_reason = self._film_evidence()

        if show_score > film_score:
            return "show", show_score - film_score >= 2, show_reason
        if film_score > show_score:
            return "film", film_score - show_score >= 2, film_reason
        return "film", False, "nothing on the disc clearly says film or show"

    def _episode_length_titles(self):
        return [
            title for title in self.titles
            if self.SHORTEST_EPISODE_SECONDS <= title["seconds"] <= self.LONGEST_EPISODE_SECONDS
        ]

    def _show_evidence(self):
        episode_titles = self._episode_length_titles()
        score = 0
        reasons = []

        if len(episode_titles) >= 3:
            score += 3
            reasons.append(f"{len(episode_titles)} titles run at episode length")
        elif self._has_matching_pair(episode_titles):
            score += 2
            reasons.append("two titles run to almost exactly the same length")

        if season_from_disc_label(self.label):
            score += 2
            reasons.append(f'the label "{self.label}" carries a season number')
        elif disc_number_from_label(self.label):
            # Weak on its own. Plenty of films ship as KNIVES_OUT_FEATURE_DISC1,
            # so this should colour the answer without driving it.
            score += 1
            reasons.append(f'the label "{self.label}" carries a disc number')

        return score, ", and ".join(reasons)

    def _has_matching_pair(self, titles):
        """Two titles within a few percent of each other look like episodes.

        Extras on a film disc vary in length; episodes do not.
        """
        lengths = sorted(title["seconds"] for title in titles)
        for shorter, longer in zip(lengths, lengths[1:]):
            if longer and abs(longer - shorter) / longer <= self.SIMILAR_LENGTH_TOLERANCE:
                return True
        return False

    def _film_evidence(self):
        feature_titles = [
            title for title in self.titles
            if title["seconds"] > self.LONGEST_EPISODE_SECONDS
        ]
        if not feature_titles:
            return 0, ""

        longest = max(title["seconds"] for title in feature_titles)
        score = 1
        reasons = [f"the longest title runs {longest // 60} minutes"]

        if len(self._episode_length_titles()) < 2:
            score += 2
            reasons.append("nothing else on the disc is episode length")

        return score, ", and ".join(reasons)


def _spaced_label(label):
    """Underscores are word characters, so \\b never fires inside FIREFLY_D1."""
    return re.sub(r"[_\-.]+", " ", label or "")


def clean_disc_label(label):
    """Turn a volume label into something worth searching for.

    Labels are shouty and full of disc bookkeeping: FIREFLY_D1, THE_WIRE_S02_D3.
    Strip the bookkeeping and hand back the words.
    """
    words = _spaced_label(label)
    words = re.sub(r"\b(disc|disk|dvd|blu\s?ray|bd|video|ts|season|series|vol(ume)?)\b", " ", words, flags=re.I)
    words = re.sub(r"\b[sd]\d{1,2}\b", " ", words, flags=re.I)
    words = re.sub(r"\bd\d{1,2}$", " ", words, flags=re.I)
    words = re.sub(r"\b\d{1,2}\b", " ", words)
    words = re.sub(r"\s+", " ", words).strip()
    return words.title()


def season_from_disc_label(label):
    """Pull a season number out of a label, or None."""
    spaced = _spaced_label(label)
    for pattern in (r"\bs(?:eason)?\s*(\d{1,2})\b", r"\bseries\s*(\d{1,2})\b"):
        match = re.search(pattern, spaced, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def disc_number_from_label(label):
    """Pull a disc number out of a label, or None."""
    match = re.search(r"\b(?:d|disc|disk)\s*(\d{1,2})\b", _spaced_label(label), flags=re.I)
    if match:
        return int(match.group(1))
    return None


def print_tsv(fields):
    print("\t".join(str(field) for field in fields))


def run_search(arguments):
    for show in TvMazeClient().search_shows(arguments.query):
        print_tsv([show["id"], show["name"], show["year"], show["network"], show["average_runtime"]])


def run_label(arguments):
    print_tsv([
        clean_disc_label(arguments.label),
        season_from_disc_label(arguments.label) or "",
        disc_number_from_label(arguments.label) or "",
    ])


def run_classify(arguments):
    disc = DiscTitles.from_tsv(sys.stdin)
    usable_titles = disc.episode_titles(0, arguments.min_length)
    kind, is_confident, reason = DiscKind(usable_titles, arguments.label).verdict()
    print_tsv([kind, "confident" if is_confident else "unsure", reason])


def run_seasons(arguments):
    episodes = TvMazeClient().episodes(arguments.show_id, arguments.order)
    counts = {}
    for episode in episodes:
        counts[episode["season"]] = counts.get(episode["season"], 0) + 1
    for season in sorted(counts):
        print_tsv([season, counts[season]])


def run_has_dvd_order(arguments):
    return 0 if TvMazeClient().has_dvd_order(arguments.show_id) else 1


def run_plan(arguments):
    client = TvMazeClient()
    all_episodes = client.episodes(arguments.show_id, arguments.order)
    if not all_episodes and arguments.order == "dvd":
        print("error\tno DVD order published for this show, use --order aired", file=sys.stderr)
        return 2

    season_episodes = [episode for episode in all_episodes if episode["season"] == arguments.season]
    if not season_episodes:
        print(f"error\tno season {arguments.season} in the {arguments.order} order", file=sys.stderr)
        return 2

    expected_episode_seconds = _expected_episode_seconds(season_episodes, arguments.expected_runtime_minutes)
    disc_titles = DiscTitles.from_tsv(sys.stdin).episode_titles(
        expected_episode_seconds, arguments.min_length
    )
    if not disc_titles:
        print("error\tno episode-length titles on this disc", file=sys.stderr)
        return 2

    preferred_start = arguments.start_episode - 1 if arguments.start_episode else None
    start_index, is_confident, note = EpisodeAlignment(season_episodes, disc_titles).best_start(preferred_start)

    print_tsv(["#plan", "confident" if is_confident else "unsure", note, len(disc_titles)])
    for offset, title in enumerate(disc_titles):
        episode_index = start_index + offset
        if episode_index >= len(season_episodes):
            print_tsv([title["title_id"], title["duration"], "", "", "unmatched"])
            continue
        episode = season_episodes[episode_index]
        print_tsv([title["title_id"], title["duration"], episode["season"], episode["number"], episode["name"]])
    return 0


def _expected_episode_seconds(season_episodes, expected_runtime_minutes):
    if expected_runtime_minutes:
        return expected_runtime_minutes * 60
    runtimes = [episode["runtime"] for episode in season_episodes if episode["runtime"]]
    if not runtimes:
        return 0
    return int(statistics.median(runtimes) * 60)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="find shows by name")
    search.add_argument("query")
    search.set_defaults(handler=run_search)

    label = commands.add_parser("label", help="read a show name, season, and disc number out of a volume label")
    label.add_argument("label")
    label.set_defaults(handler=run_label)

    classify = commands.add_parser("classify", help="say whether the disc titles on stdin are a film or a show")
    classify.add_argument("--label", default="")
    classify.add_argument("--min-length", type=int, default=300)
    classify.set_defaults(handler=run_classify)

    seasons = commands.add_parser("seasons", help="list each season and how many episodes it has")
    seasons.add_argument("--show-id", type=int, required=True)
    seasons.add_argument("--order", choices=("aired", "dvd"), default="aired")
    seasons.set_defaults(handler=run_seasons)

    dvd_order = commands.add_parser("has-dvd-order", help="exit 0 when the show publishes a DVD running order")
    dvd_order.add_argument("show_id", type=int)
    dvd_order.set_defaults(handler=run_has_dvd_order)

    plan = commands.add_parser("plan", help="map the disc titles on stdin to episodes")
    plan.add_argument("--show-id", type=int, required=True)
    plan.add_argument("--season", type=int, required=True)
    plan.add_argument("--order", choices=("aired", "dvd"), default="aired")
    plan.add_argument("--start-episode", type=int, default=0)
    plan.add_argument("--min-length", type=int, default=600)
    plan.add_argument("--expected-runtime-minutes", type=int, default=0)
    plan.set_defaults(handler=run_plan)

    return parser


def main():
    arguments = build_parser().parse_args()
    try:
        return arguments.handler(arguments) or 0
    except urllib.error.URLError as error:
        print(f"error\tcould not reach TVmaze: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
