# P-card Audit Explorer

A local Windows application for reviewing P-card transactions and potential control exceptions.

## Launch the app

1. Extract the complete ZIP file to a folder on your computer.
2. Confirm that `pcards.db` remains in that folder.
3. Double-click **Start P-card Audit Explorer.bat**.
4. The app will open automatically at `http://127.0.0.1:5000`.

The first launch creates a private local environment and installs the one requirement (Flask). This requires Python 3 and an internet connection. Later launches use the same local environment.

Keep the separate **P-card Audit Explorer server** window open while using the app. Closing that window stops the local app.

## Features

- Search and filter transactions by year, employee, vendor, MCC, amount, and dates.
- Click a transaction row to open its full details.
- Run one-click checks for annual, monthly, and per-transaction limits; same-vendor split-purchase patterns; and round-number patterns.
- See potential control exceptions in red and review patterns or literal prohibited-purchase keyword matches in amber.
- Search prohibited-purchase keywords separately in the Description and Vendor fields.

All flags are potential follow-up items, not confirmed violations.

## Optional AI API

The AI Assignment Assistant uses the OpenAI Responses API to turn an assignment question into a read-only SQLite query. The AI receives the question and the approved table schema only. The generated query runs against `pcards.db` locally, so transaction rows are not sent to the API.

Before launching the app, set your own API key in Windows Command Prompt:

```bat
setx OPENAI_API_KEY "your_api_key_here"
```

Close and reopen Command Prompt after setting the key, then start the app. You may optionally set `OPENAI_MODEL` if your account uses a different supported model. Never put an API key in the project files or upload it to GitHub.
