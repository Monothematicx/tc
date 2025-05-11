from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import csv
import os
import difflib  # For partial match calculation
import json

app = Flask(__name__)
app.secret_key = "transaction_analyser_secret_key"  # Required for session

transactions = []  # Store uploaded transactions
csv_headers = []  # Store CSV headers

# Ensure upload folder exists
os.makedirs("uploads", exist_ok=True)


def read_csv_headers(file_path):
    """Read and return just the headers of a CSV file"""
    with open(file_path, mode="r", encoding="utf-8") as file:
        reader = csv.reader(file)
        headers = next(reader)  # Get the first row (headers)
        return headers


def read_transactions(file_path):
    """Read all raw transactions with original column names"""
    trans = []
    with open(file_path, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames
        for row in reader:
            # Store each row as a dictionary with original column names
            trans.append(dict(row))
    return trans, headers


def calculate_confidence(term, counter_party):
    """Calculates the confidence level between the term and the counter-party.
    Returns a percentage (0-100) indicating how closely the term matches the counter-party.
    """
    # Handle edge cases
    if not term or not counter_party:
        return 0

    # Check if term is contained in counter_party
    if term.lower() in counter_party.lower():
        # If term is exactly equal to counter_party, return 100%
        if term.lower() == counter_party.lower():
            return 100
        # Otherwise, return percentage based on term length compared to counter_party length
        # This rewards longer, more specific matches
        term_length = len(term)
        counter_party_length = len(counter_party)
        length_ratio = min(term_length / counter_party_length, 1.0)
        return round(
            70 +
            (30 * length_ratio))  # Base 70% for contained matches, up to 100%

    # Use sequence matcher for partial matches
    sequence_matcher = difflib.SequenceMatcher(None, term.lower(),
                                               counter_party.lower())
    ratio = sequence_matcher.ratio()  # Similarity ratio (0 to 1)

    # Scale the ratio to be more meaningful (0-60% range for partial matches)
    # This ensures that substring matches always rank higher than partial text matches
    confidence = round(ratio * 60)

    return confidence


def read_keywords(file_path):
    keywords = {}
    if not os.path.exists(file_path):
        return keywords
    with open(file_path, mode="r", encoding="utf-8") as file:
        reader = csv.reader(file)
        for row in reader:
            if len(row) == 2:
                category, items = row
                keywords[category] = [i.strip() for i in items.split(",")]
    return keywords


def save_keywords(keywords, file_path):
    with open(file_path, mode="w", encoding="utf-8", newline='') as file:
        writer = csv.writer(file)
        for category, items in keywords.items():
            writer.writerow([category, ",".join(items)])


def categorize_transactions(transactions, desc_column, keywords):
    """Categorise transactions based on the description column and keywords"""
    for txn in transactions:
        txn["__category"] = "Uncategorized"  # Default category
        txn["__confidence"] = 0  # Default confidence
        best_match_term = ""

        # Skip if description column wasn't properly selected
        if not desc_column or desc_column not in txn:
            continue

        description = txn[desc_column]
        if not description:  # Skip empty descriptions
            continue

        # Track best match across all categories
        best_category = "Uncategorized"
        best_confidence = 0

        # Store category-specific confidence levels
        category_confidences = {}

        # Check each category's keywords against the description
        for category, terms in keywords.items():
            category_confidences[category] = 0

            for term in terms:
                if not term:  # Skip empty terms
                    continue

                # Calculate confidence for this term
                confidence = calculate_confidence(term, description)

                # Update this category's confidence with the best match
                if confidence > category_confidences[category]:
                    category_confidences[category] = min(confidence,
                                                         100)  # Cap at 100%

                # Keep track of overall best match
                if confidence > best_confidence:
                    best_confidence = min(confidence, 100)  # Cap at 100%
                    best_category = category
                    best_match_term = term

        # Set the best match found
        txn["__category"] = best_category
        txn["__confidence"] = best_confidence
        txn["__matched_term"] = best_match_term if best_confidence > 0 else ""

        # Store all category confidences for reporting (optional)
        txn["__category_confidences"] = category_confidences

    return transactions


def normalize_amount(transactions, amount_config):
    """Process transactions to add a normalized __amount field"""
    for txn in transactions:
        # Initialize the normalized amount
        txn["__amount"] = 0

        # Handle single amount column
        if amount_config["type"] == "single" and amount_config["column"] in txn:
            # Try to convert to float, handling possible formatting issues
            try:
                amount_str = txn[amount_config["column"]].replace(',', '')
                txn["__amount"] = float(amount_str)
            except (ValueError, TypeError):
                pass

        # Handle separate debit/credit columns
        elif amount_config["type"] == "split":
            debit_col = amount_config.get("debit_column")
            credit_col = amount_config.get("credit_column")

            # Process debit (negative amount)
            if debit_col and debit_col in txn and txn[debit_col]:
                try:
                    debit_str = txn[debit_col].replace(',', '')
                    # Debit is negative (money out)
                    debit_amount = float(debit_str) * -1 if debit_str else 0
                    txn["__amount"] += debit_amount
                except (ValueError, TypeError):
                    pass

            # Process credit (positive amount)
            if credit_col and credit_col in txn and txn[credit_col]:
                try:
                    credit_str = txn[credit_col].replace(',', '')
                    # Credit is positive (money in)
                    credit_amount = float(credit_str) if credit_str else 0
                    txn["__amount"] += credit_amount
                except (ValueError, TypeError):
                    pass

    return transactions


def save_transactions(file_path, trans):
    """Save processed transactions with added metadata fields"""
    if not trans:
        return

    # Get all original headers plus our added fields
    headers = list(trans[0].keys())

    with open(file_path, mode="w", encoding="utf-8", newline='') as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(trans)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files['csv_file']
    if file and file.filename.endswith('.csv'):
        path = os.path.join("uploads", file.filename)
        file.save(path)

        # Read CSV headers for column selection
        global csv_headers
        csv_headers = read_csv_headers(path)

        # Store the file path in session for later processing
        session['csv_file_path'] = path

        return redirect(url_for("select_columns"))

    return "Invalid file format", 400


@app.route("/select_columns", methods=["GET", "POST"])
def select_columns():
    global csv_headers

    if request.method == "POST":
        # Get user's column selections
        description_column = request.form.get("description_column")
        amount_type = request.form.get("amount_type")

        # Build the amount configuration based on selection type
        amount_config = {"type": amount_type}

        if amount_type == "single":
            amount_config["column"] = request.form.get("amount_column")
        else:  # Split type (debit/credit)
            amount_config["debit_column"] = request.form.get("debit_column")
            amount_config["credit_column"] = request.form.get("credit_column")

        # Store the column mappings in session
        session['column_mappings'] = {
            "description_column": description_column,
            "amount_config": amount_config
        }

        # Now process the full file with the column mappings
        process_transactions()

        return redirect(url_for("transactions_page"))

    # GET request - show form for column selection
    return render_template("select_columns.html", headers=csv_headers)


def process_transactions():
    """Process the uploaded CSV with the selected column mappings"""
    global transactions

    # Get file path and column mappings from session
    file_path = session.get('csv_file_path')
    column_mappings = session.get('column_mappings', {})

    if not file_path or not column_mappings:
        return redirect(url_for("index"))

    # Read raw transactions with all columns
    transactions, headers = read_transactions(file_path)

    # Get mapping info
    description_column = column_mappings.get('description_column')
    amount_config = column_mappings.get('amount_config', {})

    # Load keywords for categorization
    keywords = read_keywords('keywords.csv')

    # Categorize transactions based on description
    transactions = categorize_transactions(transactions, description_column,
                                           keywords)

    # Normalize amount values
    transactions = normalize_amount(transactions, amount_config)

    # Save processed transactions
    save_transactions("transactions.csv", transactions)


@app.route("/transactions")
def transactions_page():
    global transactions

    # Get column mappings for template
    column_mappings = session.get('column_mappings', {})
    description_column = column_mappings.get('description_column', '')

    return render_template("transactions.html",
                           transactions=transactions,
                           description_column=description_column)


@app.route("/categorize/<int:index>", methods=["POST"])
def categorize_transaction_manually(index):
    category = request.form["category"]

    # Get column mappings from session
    column_mappings = session.get('column_mappings', {})
    description_column = column_mappings.get('description_column', '')

    if 0 <= index < len(transactions):
        # Update category and confidence
        transactions[index]["__category"] = category
        transactions[index][
            "__confidence"] = 100  # Manual categorisation = 100% confidence

        # Update keywords if we have a valid description column
        if description_column and description_column in transactions[index]:
            description = transactions[index][description_column]

            # Add this description to keywords for future matching
            keywords = read_keywords("keywords.csv")
            if category not in keywords:
                keywords[category] = [description]
            else:
                if description not in keywords[category]:
                    keywords[category].append(description)

            save_keywords(keywords, "keywords.csv")

        # Save updated transactions
        save_transactions("transactions.csv", transactions)

    return redirect(url_for("transactions_page"))


@app.route("/manage_keywords")
def manage_keywords():
    keywords = read_keywords("keywords.csv")
    return render_template("keywords.html", keywords=keywords)


@app.route("/add_keyword", methods=["POST"])
def add_keyword():
    category = request.form["category"]
    keyword = request.form["keyword"]

    keywords = read_keywords("keywords.csv")
    if category not in keywords:
        keywords[category] = [keyword]
    else:
        if keyword not in keywords[category]:
            keywords[category].append(keyword)

    save_keywords(keywords, "keywords.csv")
    return redirect(url_for("manage_keywords"))


@app.route("/update_keyword", methods=["POST"])
def update_keyword():
    data = request.json
    category = data["category"]
    old_keyword = data["old_keyword"]
    new_keyword = data["new_keyword"]

    keywords = read_keywords("keywords.csv")
    if category in keywords and old_keyword in keywords[category]:
        # Replace the old keyword with the new one
        keywords[category] = [
            new_keyword if k == old_keyword else k for k in keywords[category]
        ]
        save_keywords(keywords, "keywords.csv")
        return jsonify({"success": True})

    return jsonify({"success": False, "error": "Keyword not found"}), 404


@app.route("/delete_keyword", methods=["POST"])
def delete_keyword():
    data = request.json
    category = data["category"]
    keyword = data["keyword"]

    keywords = read_keywords("keywords.csv")
    if category in keywords and keyword in keywords[category]:
        # Remove the keyword
        keywords[category].remove(keyword)

        # If this was the last keyword in the category, remove the category
        if not keywords[category]:
            del keywords[category]

        save_keywords(keywords, "keywords.csv")
        return jsonify({"success": True})

    return jsonify({"success": False, "error": "Keyword not found"}), 404


@app.route("/report")
def report():
    global transactions

    # Get column mappings for template
    column_mappings = session.get('column_mappings', {})

    # Create summary by category
    category_summary = {}
    for txn in transactions:
        category = txn.get("__category", "Uncategorized")
        amount = txn.get("__amount", 0)

        if category not in category_summary:
            category_summary[category] = 0

        category_summary[category] += amount

    # Get all unique categories for pivot table
    all_categories = set()
    for txn in transactions:
        all_categories.add(txn.get("__category", "Uncategorized"))

    # Sort categories alphabetically with "Uncategorized" at the end
    sorted_categories = sorted(
        [cat for cat in all_categories if cat != "Uncategorized"])
    if "Uncategorized" in all_categories:
        sorted_categories.append("Uncategorized")

    return render_template("report.html",
                           transactions=transactions,
                           category_summary=category_summary,
                           all_categories=sorted_categories)


@app.route("/pivot_report")
def pivot_report():
    global transactions

    # Get column mappings for template
    column_mappings = session.get('column_mappings', {})
    description_column = column_mappings.get('description_column', '')

    # Get all unique categories
    all_categories = set()
    for txn in transactions:
        all_categories.add(txn.get("__category", "Uncategorized"))

    # Sort categories alphabetically with "Uncategorized" at the end
    sorted_categories = sorted(
        [cat for cat in all_categories if cat != "Uncategorized"])
    if "Uncategorized" in all_categories:
        sorted_categories.append("Uncategorized")

    # Create category totals for the pivot table footer
    category_totals = {category: 0 for category in sorted_categories}

    # Process transactions for proper display in pivot table
    for txn in transactions:
        category = txn.get("__category", "Uncategorized")
        amount = txn.get("__amount", 0)
        category_totals[category] += amount

    # Calculate grand total
    grand_total = sum(category_totals.values())

    return render_template("pivot_report.html",
                           transactions=transactions,
                           all_categories=sorted_categories,
                           category_totals=category_totals,
                           grand_total=grand_total,
                           description_column=description_column)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
