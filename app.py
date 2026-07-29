from __future__ import annotations
import ast
import csv
import random
from datetime import datetime
from pathlib import Path
from flask import Flask, flash, redirect, render_template, request, session, url_for
app = Flask(__name__)
app.secret_key = "steam-recommender-dev"
submissions: list[dict] = []
def discover_metacritic_file() -> Path | None:
    candidate = Path(__file__).resolve().parent / "games_with_metacritic.csv"
    if candidate.exists():
        return candidate
    return None
METACRITIC_FILE = discover_metacritic_file()
def resolve_field(row: dict, names: list[str]) -> str:
    normalized = {str(key).strip().lower(): value for key, value in row.items() if key is not None}
    for name in names:
        key = str(name).strip().lower()
        if key in normalized:
            return str(normalized[key] or "")
    return ""
def parse_list_field(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, dict):
            return [str(key).strip() for key in parsed.keys() if str(key).strip()]
        if isinstance(parsed, (list, tuple)):
            return [str(item).strip() for item in parsed if str(item).strip()]
        if isinstance(parsed, str):
            return [parsed.strip()] if parsed.strip() else []
    except (SyntaxError, ValueError):
        pass

    if "," in text and text.count(",") > 1:
        return [item.strip() for item in text.replace(";", ",").split(",") if item.strip()]
    return [text.replace(";", ",").strip()] if text.replace(";", ",").strip() else []
def normalize_game(row: dict) -> dict:
    name = (resolve_field(row, ["name", "Name", "title"]) or "").strip()
    if not name:
        return {}
    price_text = (resolve_field(row, ["price", "Price"]) or "0").strip()
    try:
        price_value = float(price_text) if price_text else 0.0
    except ValueError:
        price_value = 0.0
    languages = parse_list_field(resolve_field(row, ["supported_languages", "Supported languages", "languages", "Languages"]))
    genres = parse_list_field(resolve_field(row, ["genres", "Genres"]))
    tags = parse_list_field(resolve_field(row, ["tags", "Tags"]))

    genre_name = genres[0] if genres else "Other"

    rating_value = 0.0
    metacritic_text = resolve_field(row, ["metacritic_score", "Metacritic score", "metacritic"]).strip()
    if metacritic_text:
        try:
            metacritic_value = float(metacritic_text)
            rating_value = round(metacritic_value, 1) if metacritic_value > 0 else 0.0
        except ValueError:
            rating_value = 0.0
    if rating_value <= 0:
        rating_text = resolve_field(row, ["user_score", "User score", "rating"]).strip()
        if rating_text.isdigit():
            rating_value = round(float(rating_text) / 20.0, 1)
        elif rating_text:
            try:
                rating_value = round(float(rating_text), 1)
            except ValueError:
                rating_value = 0.0

    image_url = resolve_field(row, ["header_image", "image", "image_url", "cover_image"]).strip()
    if not image_url:
        image_url = ""
    return {
        "name": name,
        "genre": genre_name,
        "languages": languages or ["English"],
        "tags": tags or ["General"],
        "rating": rating_value or 3.0,
        "price": price_value,
        "image_url": image_url,
    }
def load_metacritic_index() -> dict[str, dict]:
    if not METACRITIC_FILE or not METACRITIC_FILE.exists():
        return {}
    index: dict[str, dict] = {}
    try:
        with METACRITIC_FILE.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                game_name = (resolve_field(row, ["name", "Name", "title"]) or "").strip()
                if not game_name:
                    continue
                index[game_name.lower()] = {
                    "rating": round(float(resolve_field(row, ["metacritic_score", "Metacritic score", "metacritic"]).strip() or 0), 1),
                    "image_url": resolve_field(row, ["header_image", "image", "image_url", "cover_image"]).strip(),
                    "price": float(resolve_field(row, ["price", "Price"]).strip() or 0.0),
                }
    except Exception:
        return {}
    return index


METACRITIC_INDEX = load_metacritic_index()


def load_games(limit: int | None = None) -> list[dict]:
    if not METACRITIC_FILE or not METACRITIC_FILE.exists():
        return []

    try:
        with METACRITIC_FILE.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            loaded_games = []
            for row in reader:
                game = normalize_game(row)
                if game:
                    meta = METACRITIC_INDEX.get(game["name"].lower(), {})
                    if meta.get("rating"):
                        game["rating"] = meta["rating"]
                    if meta.get("image_url"):
                        game["image_url"] = meta["image_url"]
                    if meta.get("price") is not None:
                        game["price"] = float(meta["price"])
                    loaded_games.append(game)
                if limit is not None and len(loaded_games) >= limit:
                    break
            return loaded_games
    except Exception:
        return []


games = load_games()


def get_filter_options() -> tuple[list[str], list[str], list[str]]:
    genres = sorted({game["genre"] for game in games if game.get("genre")})
    languages = sorted({language for game in games for language in game.get("languages", [])}, key=str.lower)
    tags = sorted({tag for game in games for tag in game.get("tags", [])}, key=str.lower)
    return genres, languages, tags


def get_profile_options() -> tuple[list[str], list[str], list[str]]:
    return get_filter_options()


def get_filtered_games(search_text: str = "", genre: str = "", language: str = "", tags: list[str] | None = None, sort_by: str = "name") -> list[dict]:
    selected_tags = [tag.lower() for tag in (tags or [])]
    filtered = []

    for game in games:
        if search_text and search_text.lower() not in game["name"].lower():
            continue
        if genre and game["genre"].lower() != genre.lower():
            continue
        if language and language.lower() not in [item.lower() for item in game["languages"]]:
            continue
        if selected_tags and not all(tag in [item.lower() for item in game["tags"]] for tag in selected_tags):
            continue
        filtered.append(game)
    if sort_by == "rating":
        filtered.sort(key=lambda item: item["rating"], reverse=True)
    elif sort_by == "price":
        filtered.sort(key=lambda item: item["price"])
    else:
        filtered.sort(key=lambda item: item["name"].lower())
    return filtered
@app.route("/")
def home():
    selected_profile_name = request.args.get("profile", "").strip()
    selected_profile = None
    if selected_profile_name:
        selected_profile = next((item for item in submissions if str(item.get("name", "")).strip().lower() == selected_profile_name.lower()), None)
    if selected_profile is None:
        selected_profile = submissions[-1] if submissions else None
    recommended_games = []
    if selected_profile:
        profile_genres = set([g.lower() for g in selected_profile.get("genres", [])])
        profile_tags = set([t.lower() for t in selected_profile.get("tags", [])])
        profile_languages = set([l.lower() for l in selected_profile.get("languages", [])])
        scored_games = []
        for game in games:
            score = 0
            if profile_genres and game.get("genre", "").lower() in profile_genres:
                score += 3
            if profile_tags:
                score += len(profile_tags.intersection([t.lower() for t in game.get("tags", [])]))
            if profile_languages:
                score += len(profile_languages.intersection([l.lower() for l in game.get("languages", [])]))
            if score > 0:
                scored_games.append((score, game))
        scored_games.sort(reverse=True, key=lambda tup: (tup[0], tup[1]["rating"], -tup[1]["price"]))
        recommended_games = [g for _, g in scored_games[:15]]
    else:
        selected_profile = None
        recommended_games = []
    featured_games = []
    if games:
        high_score_games = [
            game for game in games
            if isinstance(game.get("rating"), (int, float)) and game.get("rating", 0) > 80
        ]
        if high_score_games:
            featured_games = random.sample(high_score_games, k=min(18, len(high_score_games)))
        else:
            featured_games = random.sample(games, k=min(18, len(games)))
    return render_template(
        "index.html",
        latest_profile=selected_profile,
        recommended_games=recommended_games,
        submissions=submissions,
        errors=[],
        featured_games=featured_games,
        selected_profile_name=selected_profile.get("name") if selected_profile else "",
    )
@app.route("/games")
def game_list():
    search_text = request.args.get("search", "").strip()
    genre = request.args.get("genre", "").strip()
    language = request.args.get("language", "").strip()
    selected_tags = request.args.getlist("tag")
    sort_by = request.args.get("sort_by", "name")

    visible_games = get_filtered_games(
        search_text=search_text,
        genre=genre,
        language=language,
        tags=selected_tags,
        sort_by=sort_by,
    )
    genres, languages, tags = get_filter_options()
    return render_template(
        "game_list.html",
        games=visible_games,
        search_text=search_text,
        genre=genre,
        language=language,
        selected_tags=selected_tags,
        sort_by=sort_by,
        genres=genres,
        languages=languages,
        tags=tags,
    )
@app.route("/about")
def about():
    return render_template("about.html")
@app.route("/add-game")
def add_game_page():
    return render_template("add_game.html")
@app.route("/profile")
def profile_view():
    return render_template("profile_view.html", submissions=submissions)
@app.route("/profile/delete/<int:index>", methods=["POST"])
def delete_profile(index: int):
    if 0 <= index < len(submissions):
        deleted_name = str(submissions[index].get("name", "")).strip() or f"Profile {index + 1}"
        del submissions[index]
        flash(f"Deleted profile '{deleted_name}'.", "success")
    else:
        flash("That profile could not be found.", "error")
    return redirect(url_for("profile_view"))


@app.route("/profile/edit/<int:index>", methods=["GET", "POST"])
def edit_profile(index: int):
    if not 0 <= index < len(submissions):
        flash("That profile could not be found.", "error")
        return redirect(url_for("profile_view"))
    profile = submissions[index]
    if request.method == "POST":
        profile["name"] = request.form.get("profile_name", "").strip() or "Untitled profile"
        profile["genres"] = request.form.getlist("genres")
        profile["languages"] = request.form.getlist("languages")
        profile["tags"] = request.form.getlist("tags")
        profile["notes"] = request.form.get("notes", "").strip()
        flash("Profile updated.", "success")
        return redirect(url_for("profile_view"))
    genres, languages, tags = get_profile_options()
    return render_template(
        "profile_wizard.html",
        step=3,
        genres=genres,
        languages=languages,
        tags=tags,
        selected_genres=profile.get("genres", []),
        selected_languages=profile.get("languages", []),
        selected_tags=profile.get("tags", []),
        profile_name=profile.get("name", ""),
        notes=profile.get("notes", ""),
        edit_index=index,
    )
@app.route("/profile/step/<int:step>", methods=["GET", "POST"])
def profile_step(step: int):
    if step not in {1, 2, 3}:
        return redirect(url_for("profile_step", step=1))
    profile_data = dict(session.get("profile_data", {}))
    if request.method == "POST":
        if step == 1:
            profile_data["genres"] = request.form.getlist("genres")
        elif step == 2:
            profile_data["languages"] = request.form.getlist("languages")
        else:
            profile_data["tags"] = request.form.getlist("tags")
            profile_data["notes"] = request.form.get("notes", "").strip()
        if step == 1 or request.form.get("profile_name"):
            profile_data["profile_name"] = request.form.get("profile_name", "").strip()
        session["profile_data"] = profile_data
        if step < 3:
            return redirect(url_for("profile_step", step=step + 1))
        profile_name = str(profile_data.get("profile_name", "") or "").strip() or "Untitled profile"
        submission = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "name": profile_name,
            "genres": profile_data.get("genres", []),
            "languages": profile_data.get("languages", []),
            "tags": profile_data.get("tags", []),
            "notes": profile_data.get("notes", ""),
        }
        submissions.append(submission)
        session.pop("profile_data", None)
        flash("Your profile preferences were saved. We will use them to shape future recommendations.", "success")
        return redirect(url_for("home"))

    genres, languages, tags = get_profile_options()
    selected_genres = profile_data.get("genres", [])
    selected_languages = profile_data.get("languages", [])
    selected_tags = profile_data.get("tags", [])
    profile_name = profile_data.get("profile_name", "")

    return render_template(
        "profile_wizard.html",
        step=step,
        genres=genres,
        languages=languages,
        tags=tags,
        selected_genres=selected_genres,
        selected_languages=selected_languages,
        selected_tags=selected_tags,
        profile_name=profile_name,
        notes=profile_data.get("notes", ""),
    )


@app.route("/submit", methods=["POST"])
def submit():
    cpu = request.form.get("cpu", "").strip()
    gpu = request.form.get("gpu", "").strip()
    ram = request.form.get("ram", "").strip()
    storage = request.form.get("storage", "").strip()
    budget = request.form.get("budget", "").strip()
    genres = request.form.getlist("genres")
    errors: list[str] = []
    if cpu or gpu or ram or storage or budget:
        if not cpu or not gpu or not ram or not storage or not budget:
            errors.append("If you want to use a profile, please complete every field before submitting.")
        try:
            ram_value = int(ram)
            storage_value = int(storage)
            budget_value = float(budget)
        except ValueError:
            errors.append("RAM, storage, and budget must be numeric values.")
            ram_value = storage_value = 0
            budget_value = 0.0
        if ram_value <= 0 or storage_value <= 0 or budget_value <= 0:
            errors.append("RAM, storage, and budget must be positive values.")
    else:
        ram_value = 0
        storage_value = 0
        budget_value = 0.0

    if errors:
        flash("Please correct the issues below.", "error")
        return render_template(
            "index.html",
            errors=errors,
            form={
                "cpu": cpu,
                "gpu": gpu,
                "ram": ram,
                "storage": storage,
                "budget": budget,
                "genres": genres,
            },
            submissions=submissions,
            games=get_filtered_games(),
            genres=get_filter_options()[0],
            languages=get_filter_options()[1],
            tags=get_filter_options()[2],
        )
    submission = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "cpu": cpu,
        "gpu": gpu,
        "ram_gb": ram_value,
        "storage_gb": storage_value,
        "budget_usd": budget_value,
        "genres": genres,
    }
    if cpu or gpu or ram or storage or budget:
        submissions.append(submission)
        flash("Your system profile has been saved. We will use it to tailor future recommendations.", "success")
    else:
        flash("You can still browse the full game list. Add a profile later if you want personalized recommendations.", "success")
    return redirect(url_for("home"))
@app.route("/add_game", methods=["POST"])
def add_game():
    name = request.form.get("game_name", "").strip()
    genre = request.form.get("game_genre", "").strip()
    rating = request.form.get("game_rating", "").strip()
    price = request.form.get("game_price", "").strip()
    languages_text = request.form.get("game_languages", "").strip()
    tags = request.form.getlist("game_tags")
    if not name or not genre:
        flash("Please add a game name and genre.", "error")
        return redirect(url_for("home"))
    try:
        rating_value = float(rating) if rating else 0.0
        price_value = float(price) if price else 0.0
    except ValueError:
        flash("Rating and price must be numeric values.", "error")
        return redirect(url_for("home"))
    languages = [item.strip() for item in languages_text.split(",") if item.strip()]
    if not languages:
        languages = ["English"]
    games.append(
        {
            "name": name,
            "genre": genre,
            "languages": languages,
            "tags": tags,
            "rating": rating_value,
            "price": price_value,
        }
    )
    flash(f"Added {name} to the game list.", "success")
    return redirect(url_for("game_list"))
if __name__ == "__main__":
    app.run(debug=True)
