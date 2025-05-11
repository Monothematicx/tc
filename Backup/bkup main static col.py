from flask import Flask, render_template, request, redirect, url_for
import csv
import os
import difflib  # For partial match calculation

app = Flask(__name__)
transactions = []  # Store uploaded transactions

# Ensure upload folder exists
os.makedirs("uploads", exist_ok=True)


def read_transactions(file_path):
    trans = []
    with open(file_path, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            trans.append([
                row.get('Date', ''),
                row.get('Counter Party', ''),
                row.get('Reference', ''),
                row.get('Type', ''),
                row.get('Amount (GBP)', ''),
                row.get('Balance (GBP)', ''),
                row.get('Spending Category', ''),
                row.get('Notes', ''),
                'Uncategorized',  # Default category
                0  # Confidence level, initialized to 0
            ])
    return trans


def save_transactions(file_path, trans):
    with open(file_path, mode="w", encoding="utf-8", newline='') as file:
        writer = csv.writer(file)
        writer.writerow([
            'Date', 'Counter Party', 'Reference', 'Type', 'Amount (GBP)',
            'Balance (GBP)', 'Spending Category', 'Notes', 'Category',
            'Confidence'
        ])
        writer.writerows(trans)


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


def calculate_confidence(term, counter_party):
    """Calculates the confidence level between the term and the counter-party."""
    if term.lower() == counter_party.lower():
        return 100  # Perfect match
    else:
        sequence_matcher = difflib.SequenceMatcher(None, term.lower(),
                                                   counter_party.lower())
        ratio = sequence_matcher.ratio()  # Similarity ratio (0 to 1)
        confidence = round(ratio * 100)  # Convert to percentage
        return confidence


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files['csv_file']
    if file and file.filename.endswith('.csv'):
        path = os.path.join("uploads", file.filename)
        file.save(path)

        global transactions
        transactions = read_transactions(path)

        # Load keywords and categories
        keywords = read_keywords('keywords.csv')

        # Categorize transactions based on the keywords and calculate confidence levels
        for txn in transactions:
            txn_category = 'Uncategorized'  # Default category if no match
            txn_confidence = 0  # Default confidence if no match
            for category, terms in keywords.items():
                for term in terms:
                    if term.lower() in txn[1].lower(
                    ):  # txn[1] should be the description
                        txn_category = category
                        txn_confidence = calculate_confidence(term, txn[1])
                        break
                if txn_category != 'Uncategorized':
                    break

            # Set the category and confidence
            txn[8] = txn_category  # Assuming category is stored in index 8
            txn[9] = txn_confidence  # Set confidence in the 9th index

        save_transactions("transactions.csv", transactions)
        return redirect(url_for("transactions_page"))

    return "Invalid file format", 400


@app.route("/transactions")
def transactions_page():
    global transactions
    return render_template("transactions.html", transactions=transactions)


@app.route("/categorize/<int:index>", methods=["POST"])
def categorize_transaction_manually(index):
    category = request.form["category"]
    transactions[index][8] = category  # Update category
    transactions[index][
        9] = 100  # Set confidence to 100% for manually categorized items

    keywords = read_keywords("keywords.csv")
    counter_party = transactions[index][1]
    if category not in keywords:
        keywords[category] = [counter_party]
    else:
        if counter_party not in keywords[category]:
            keywords[category].append(counter_party)

    save_keywords(keywords, "keywords.csv")
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


@app.route("/report")
def report():
    global transactions
    return render_template("report.html", transactions=transactions)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
