# Photos and Memories

A private Streamlit app for collecting photographs and memories for a wake.

- Public page: family and friends submit one photo at a time.
- Supabase Storage: securely stores the uploaded image.
- Supabase Postgres: stores the submitter, people in the photo, date, location, memory and approval status.
- Curator dashboard: review, approve, reorder and export photos and captions.

## Local setup

1. Install Python 3.10 or newer.
2. Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Copy the example secrets file:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

5. Add your Supabase project URL, anon key, service-role key, bucket name and curator password.

6. Run the app:

```bash
streamlit run app.py
```

## Supabase setup

1. Create a new Supabase project.
2. Open SQL Editor.
3. Paste and run `supabase/schema.sql`.
4. Confirm that the `wake-photos` bucket exists.
5. Copy the Project URL, anon key and service-role key from Project Settings -> API.
6. Add those values to `.streamlit/secrets.toml`.

## GitHub deployment

1. Create a new empty GitHub repository.
2. Initialise Git locally:

```bash
git init
git add .
git commit -m "Initial wake photo collection app"
git branch -M main
git remote add origin [https://github.com/YOUR_USERNAME/wake-photo-app.git](https://github.com/YOUR_USERNAME/wake-photo-app.git)
git push -u origin main
```

3. Go to Streamlit Community Cloud.
4. Create a new app from the GitHub repository.
5. Set the main file path to `app.py`.
6. Before deploying, open **Advanced settings** and add the same secrets from `.streamlit/secrets.toml`.
7. Deploy the app.
8. Share only the public upload URL with family.
9. Keep the curator dashboard URL private.

## Security

- Never commit `.streamlit/secrets.toml`.
- Never expose the Supabase service-role key publicly.
- The public app can only create submissions and upload into `pending/`.
- The curator dashboard requires a password and uses the service-role key.
- Review every submission before displaying it at the wake.
