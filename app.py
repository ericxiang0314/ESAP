from __future__ import annotations
import ast
import csv
import os
import random
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, url_for
from pymongo import MongoClient
app = Flask(__name__)
app.secret_key = "steam-recommender-dev"
load_dotenv()
# Connect to MongoDB database when a connection string is provided.
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_AVAILABLE = False
mongo_client = None
mongo_collection = None
if MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_collection = mongo_client["games"]["steam_games"]
        mongo_client.admin.command("ping")
        MONGO_AVAILABLE = True
    except Exception:
        mongo_client = None
        mongo_collection = None
        MONGO_AVAILABLE = False
submissions: list[dict] = []
METACRITIC_FILE = None
KEYWORDS_FILE = None
DATASET_FILE = Path(__file__).resolve().parent / "games_with_metacritic.csv"
def resolve_field(row: dict, names: list[str]) -> str:
    # Match column names safely.
    normalized = {str(key).strip().lower(): value for key, value in row.items() if key is not None}
    for name in names:
        key = str(name).strip().lower()
        if key in normalized:
            return str(normalized[key] or "")
    return ""
def parse_list_field(value: str) -> list[str]:
    # Parse string lists cleanly.
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
            text = parsed.strip()
    except (SyntaxError, ValueError):
        pass
    if not text:
        return []
    for separator in ["|", ";", ","]:
        if separator in text:
            parts = [item.strip() for item in text.split(separator) if item.strip()]
            if len(parts) > 1:
                return parts
    return [text.strip()] if text.strip() else []
def make_page_name(name: str) -> str:
    # Build a URL-friendly name.
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
def normalize_game(row: dict) -> dict:
    """Convert a raw MongoDB document or row into the shape expected by the templates."""
    # Skip rows without names.
    name = (resolve_field(row, ["name", "Name", "title"]) or "").strip()
    if not name:
        return {}
    appid = resolve_field(row, ["appid", "app_id"]).strip()
    # Parse the price into a float.
    price_text = (resolve_field(row, ["price", "Price"]) or "0").strip()
    try:
        price_value = float(price_text) if price_text else 0.0
    except ValueError:
        price_value = 0.0
    # Normalize lists for languages, genres, and tags.
    languages = parse_list_field(resolve_field(row, ["supported_languages", "Supported languages", "languages", "Languages"]))
    genres = parse_list_field(resolve_field(row, ["genres", "Genres"]))
    tags = parse_list_field(resolve_field(row, ["tags", "Tags"]))
    genre_name = genres[0] if genres else "Other"
    # Use the Metacritic score when it exists.
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
    # Pull the cover image when one is present.
    image_url = resolve_field(row, ["header_image", "image", "image_url", "cover_image"]).strip()
    if not image_url:
        image_url = ""
    return {
        "appid": appid,
        "name": name,
        "genre": genre_name,
        "languages": languages or ["English"],
        "tags": tags or ["General"],
        "rating": rating_value or 3.0,
        "price": price_value,
        "image_url": image_url,
    }
# Use a stable key so duplicate games are filtered out consistently.
def make_game_key(game: dict) -> str:
    # Build a stable key.
    appid = str(game.get("appid") or "").strip()
    if appid:
        return f"appid:{appid}"
    name = str(game.get("name") or "").strip().lower()
    return f"name:{name}"
def deduplicate_games(game_list: list[dict]) -> list[dict]:
    # Remove repeated entries.
    seen_keys: set[str] = set()
    unique_games: list[dict] = []
    for game in game_list:
        if not game:
            continue
        key = make_game_key(game)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_games.append(game)
    return unique_games
def load_games_from_mongodb(limit: int | None = None) -> list[dict]:
    """Load the catalog from MongoDB Atlas when the connection is available."""
    # Skip MongoDB without connection.
    if not MONGO_AVAILABLE or mongo_collection is None:
        return []
    try:
        cursor = mongo_collection.find({}, {"_id": 0})
        if limit is not None:
            cursor = cursor.limit(limit)
        loaded_games = []
        for document in cursor:
            game = normalize_game(document)
            if game:
                game["short_description"] = resolve_field(document, ["short_description", "about_the_game", "detailed_description"]).strip() or ""
                game["top_critic_review"] = resolve_field(document, ["top_critic_review", "reviews"]).strip() or ""
                game["top_user_review"] = resolve_field(document, ["top_user_review", "user_score"]).strip() or ""
                game["positive_keywords"] = parse_list_field(resolve_field(document, ["positive_keywords"]))
                game["negative_keywords"] = parse_list_field(resolve_field(document, ["negative_keywords"]))
                game["keyword_appid"] = resolve_field(document, ["appid", "app_id"]).strip()
                loaded_games.append(game)
        return deduplicate_games(loaded_games)
    except Exception:
        return []
def load_games_from_csv(limit: int | None = None) -> list[dict]:
    """Load the full game catalog from the workspace CSV file when MongoDB is unavailable."""
    # Stop if dataset is missing.
    if not DATASET_FILE.exists():
        return []
    try:
        with DATASET_FILE.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.DictReader(handle)
            loaded_games = []
            for row in reader:
                # Convert each row into the app's game format.
                game = normalize_game(row)
                if game:
                    game["short_description"] = resolve_field(row, ["short_description", "about_the_game", "detailed_description"]).strip() or ""
                    game["top_critic_review"] = resolve_field(row, ["top_critic_review", "reviews"]).strip() or ""
                    game["top_user_review"] = resolve_field(row, ["top_user_review", "user_score"]).strip() or ""
                    game["positive_keywords"] = parse_list_field(resolve_field(row, ["positive_keywords"]))
                    game["negative_keywords"] = parse_list_field(resolve_field(row, ["negative_keywords"]))
                    game["keyword_appid"] = resolve_field(row, ["appid", "app_id"]).strip()
                    loaded_games.append(game)
                if limit is not None and len(loaded_games) >= limit:
                    break
            return deduplicate_games(loaded_games)
    except Exception:
        return []


def load_games(limit: int | None = None) -> list[dict]:
    """Prefer the MongoDB catalog and fall back to the full CSV dataset if the database is unavailable."""
    # Try MongoDB first.
    mongo_games = load_games_from_mongodb(limit=limit)
    if mongo_games:
        return mongo_games
    return load_games_from_csv(limit=limit)
games = deduplicate_games(load_games())
def get_filter_options() -> tuple[list[str], list[str], list[str]]:
    # Build filter choices.
    genres = sorted({game["genre"] for game in games if game.get("genre")})
    languages = sorted({language for game in games for language in game.get("languages", [])}, key=str.lower)
    tags = sorted({tag for game in games for tag in game.get("tags", [])}, key=str.lower)
    return genres, languages, tags
def get_profile_options() -> tuple[list[str], list[str], list[str]]:
    return get_filter_options()
def get_filtered_games(search_text: str = "", genre: str = "", language: str = "", tags: list[str] | None = None, sort_by: str = "name") -> list[dict]:
    """Filter and sort the current catalog for the browse page."""
    # Normalize requested tags.
    selected_tags = [tag.lower() for tag in (tags or [])]
    filtered = []
    seen_keys: set[str] = set()
    for game in games:
        key = make_game_key(game)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        # Skip games that do not match the search text.
        if search_text and search_text.lower() not in game["name"].lower():
            continue
        # Skip games in a different genre.
        if genre and game["genre"].lower() != genre.lower():
            continue
        # Skip games that do not support the chosen language.
        if language and language.lower() not in [item.lower() for item in game["languages"]]:
            continue
        # Skip games that miss any required tag.
        if selected_tags and not all(tag in [item.lower() for item in game["tags"]] for tag in selected_tags):
            continue
        filtered.append(game)
    # Apply the chosen sort order.
    if sort_by == "rating":
        filtered.sort(key=lambda item: item["rating"], reverse=True)
    elif sort_by == "price":
        filtered.sort(key=lambda item: item["price"])
    else:
        filtered.sort(key=lambda item: item["name"].lower())
    return filtered
@app.route("/")
def home():
    """Render the home page and choose the recommended games for the active profile."""
    # Render home page.
    selected_profile = submissions[-1] if submissions else None
    recommended_games = []
    if selected_profile:
        # Score games by matching the profile's genres, tags, and languages.
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
    # Choose a small featured set from the highest-rated games.
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
    )
@app.route("/games")
def game_list():
    # Read filter values.
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
@app.route("/game/<path:game_path>")
def game_detail(game_path: str):
    # Split the route safely.
    parts = [part for part in game_path.split("/") if part]
    appid = None
    page_name = game_path
    if parts and parts[0].isdigit():
        appid = parts[0]
        page_name = "/".join(parts[1:]) if len(parts) > 1 else ""
    if appid:
        # Use direct appid lookup.
        matches = [game for game in games if str(game.get("appid", "")).strip() == appid]
    else:
        matches = []
    if not matches:
        # Fall back to name lookup.
        target_name = make_page_name(page_name or game_path)
        matches = [
            game
            for game in games
            if make_page_name(game.get("name", "")) == target_name
        ]

    if not matches:
        flash("That game could not be found.", "error")
        return redirect(url_for("game_list"))

    game = matches[0]
    rating = game.get("rating")
    metacritic_value = rating if isinstance(rating, (int, float)) and rating not in (0, 3.0) else "N/A"

    return render_template(
        "game_detail.html",
        game=game,
        short_description=game.get("short_description", ""),
        metacritic_score=metacritic_value,
        top_critic_review=game.get("top_critic_review", "No critic review available."),
        top_user_review=game.get("top_user_review", "No user review available."),
        positive_keywords=game.get("positive_keywords", []),
        negative_keywords=game.get("negative_keywords", []),
    )
@app.route("/about")
def about():
    return render_template("about.html")
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
    if not (0 <= index < len(submissions)):
        flash("That profile could not be found.", "error")
        return redirect(url_for("profile_view"))
    profile = submissions[index]
    if request.method == "POST":
        profile["name"] = (
            request.form.get("profile_name", "").strip()
            or "Untitled profile"
        )
        profile["genres"] = request.form.getlist("genres")
        profile["languages"] = request.form.getlist("languages")
        profile["tags"] = request.form.getlist("tags")
        flash("Profile updated.", "success")
        return redirect(url_for("profile_view"))
    genres, languages, tags = get_profile_options()
    return render_template(
        "profile_setup.html",
        step=3,
        genres=genres,
        languages=languages,
        tags=tags,
        selected_genres=profile.get("genres", []),
        selected_languages=profile.get("languages", []),
        selected_tags=profile.get("tags", []),
        profile_name=profile.get("name", ""),
        edit_index=index,
    )
@app.route("/profile/step/<int:step>", methods=["GET", "POST"])
def profile_step(step: int):
    # Guard invalid steps.
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
        if step == 1 or request.form.get("profile_name"):
            profile_data["profile_name"] = (
                request.form.get("profile_name", "").strip()
            )
        session["profile_data"] = profile_data
        if step < 3:
            # Move to the next profile step.
            return redirect(url_for("profile_step", step=step + 1))
        profile_name = (
            str(profile_data.get("profile_name", "") or "").strip()
            or "Untitled profile"
        )
        submission = {
            "timestamp": datetime.now().astimezone().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "name": profile_name,
            "genres": profile_data.get("genres", []),
            "languages": profile_data.get("languages", []),
            "tags": profile_data.get("tags", []),
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
        "profile_setup.html",
        step=step,
        genres=genres,
        languages=languages,
        tags=tags,
        selected_genres=selected_genres,
        selected_languages=selected_languages,
        selected_tags=selected_tags,
        profile_name=profile_name,
    )
@app.route("/submit", methods=["POST"])
def submit():
    # Handle legacy form data.
    cpu = request.form.get("cpu", "").strip()
    gpu = request.form.get("gpu", "").strip()
    ram = request.form.get("ram", "").strip()
    storage = request.form.get("storage", "").strip()
    budget = request.form.get("budget", "").strip()
    genres = request.form.getlist("genres")
    errors: list[str] = []
    # Validate the optional system fields when they are provided.
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
            # Reject impossible or empty numeric values.
            errors.append("RAM, storage, and budget must be positive values.")
    else:
        ram_value = 0
        storage_value = 0
        budget_value = 0.0
    if errors:
        # Show the form again when validation fails.
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
        "timestamp": datetime.now().astimezone().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "cpu": cpu,
        "gpu": gpu,
        "ram_gb": ram_value,
        "storage_gb": storage_value,
        "budget_usd": budget_value,
        "genres": genres,
    }
    if cpu or gpu or ram or storage or budget:
        # Save the completed profile to the in-memory list.
        submissions.append(submission)
        flash("Your system profile has been saved. We will use it to tailor future recommendations.", "success")
    else:
        flash("You can still browse the full game list. Add a profile later if you want personalized recommendations.", "success")
    return redirect(url_for("home"))
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
