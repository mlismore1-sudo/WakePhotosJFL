import io
import os
import uuid
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.set_page_config(
    page_title="Photos and Memories",
    page_icon="📷",
    layout="wide",
)

ALLOWED_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_FILE_MB = 25


@st.cache_resource
def get_public_client() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_ANON_KEY"],
    )


def get_service_client() -> Client:
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_SERVICE_ROLE_KEY"],
    )


def safe_filename(filename: str) -> str:
    base = os.path.basename(filename)
    return "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in base
    ).lower() or "photo.jpg"


def make_caption(row) -> str:
    people = row.get("people_in_photo") or ""
    date_taken = row.get("date_taken") or ""
    location = row.get("location") or ""

    parts = [part for part in [date_taken, location] if part]
    context = " — ".join(parts)

    caption = people
    if context:
        caption += f"\n{context}"

    memory = row.get("memory")
    if memory:
        caption += f'\n“{memory}”'

    submitter = row.get("submitter_name")
    if submitter:
        caption += f"\nShared by {submitter}"

    return caption


def render_public_upload():
    st.title("📷 Photos and Memories")
    st.markdown(
        """
        Thank you for sharing a photo or memory.

        Please upload photographs from any part of Dad’s life — family occasions,
        holidays, work, friendships, sport, everyday moments, or photos we may
        never have seen.

        Approximate dates and partial information are completely fine.
        Please upload **one photo per submission** where possible, so each photo
        can be correctly labelled.
        """
    )

    with st.form("photo_submission", clear_on_submit=True):
        st.subheader("About you")

        submitter_name = st.text_input(
            "Your name *",
            placeholder="For example: Sarah Smith",
        )

        submitter_relationship = st.text_input(
            "Your relationship to Dad",
            placeholder="For example: daughter, friend, colleague",
        )

        submitter_contact = st.text_input(
            "Your phone number or email address",
            help="Optional. Useful only if we need to check a detail.",
        )

        st.subheader("About the photo")

        people_in_photo = st.text_area(
            "Who is in the photo? *",
            placeholder=(
                "For example: Dad with Mum, Sarah and James at Christmas 1998."
            ),
            height=100,
        )

        date_taken = st.text_input(
            "When was the photo taken?",
            placeholder="For example: about 1998, the 1980s, summer 2004",
        )

        location = st.text_input(
            "Where was it taken?",
            placeholder="For example: home, Cornwall, wedding venue",
        )

        memory = st.text_area(
            "Is there a memory or story connected to this photo?",
            placeholder=(
                "Please share anything you remember — a funny moment, what Dad "
                "was like, why the photo matters, or a message you would like included."
            ),
            height=140,
        )

        display_permission = st.radio(
            "Are you happy for this photo and information to be displayed at Dad’s wake?",
            options=["Yes", "Yes, but please contact me first", "No — please keep it private"],
            index=0,
        )

        st.subheader("Upload your photo")

        uploaded_file = st.file_uploader(
            "Choose a photo *",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=False,
        )

        if uploaded_file is not None:
            st.image(uploaded_file, caption="Preview", use_container_width=True)

        submitted = st.form_submit_button("Submit photo", use_container_width=True)

    if not submitted:
        return

    errors = []

    if not submitter_name.strip():
        errors.append("Please enter your name.")
    if not people_in_photo.strip():
        errors.append("Please say who is in the photo.")
    if uploaded_file is None:
        errors.append("Please choose a photo to upload.")

    if uploaded_file is not None:
        if uploaded_file.type not in ALLOWED_TYPES:
            errors.append("Please upload a JPG, JPEG, PNG or WEBP image.")
        if uploaded_file.size > MAX_FILE_MB * 1024 * 1024:
            errors.append(f"Please upload an image smaller than {MAX_FILE_MB} MB.")

    if errors:
        for error in errors:
            st.error(error)
        return

    submission_id = str(uuid.uuid4())
    filename = safe_filename(uploaded_file.name)
    storage_path = f"pending/{submission_id}/{filename}"

    public_client = get_public_client()

    try:
        with st.spinner("Uploading photo..."):
            public_client.storage.from_(st.secrets["SUPABASE_BUCKET"]).upload(
                file=uploaded_file.getvalue(),
                path=storage_path,
                file_options={
                    "content-type": uploaded_file.type,
                    "cache-control": "3600",
                    "upsert": "false",
                },
            )

        record = {
            "id": submission_id,
            "submitter_name": submitter_name.strip(),
            "submitter_relationship": submitter_relationship.strip() or None,
            "submitter_contact": submitter_contact.strip() or None,
            "people_in_photo": people_in_photo.strip(),
            "date_taken": date_taken.strip() or None,
            "location": location.strip() or None,
            "memory": memory.strip() or None,
            "display_permission": display_permission,
            "photo_path": storage_path,
            "status": "pending",
        }

        public_client.table("photo_submissions").insert(record).execute()

        st.success(
            "Thank you — your photo has been received. "
            "If you have more photos, please submit them separately."
        )

    except Exception as error:
        st.error(
            "Sorry, we could not save your submission. "
            "Please try again, or email the photo to the family."
        )
        st.exception(error)


def curator_login() -> bool:
    st.title("🔐 Curator dashboard")

    password = st.text_input("Curator password", type="password")

    if not password:
        st.info("Enter the curator password to continue.")
        return False

    if password != st.secrets["CURATOR_PASSWORD"]:
        st.error("Incorrect password.")
        return False

    return True


def render_curator_dashboard():
    st.title("📋 Photo submissions")

    service_client = get_service_client()
    bucket = st.secrets["SUPABASE_BUCKET"]

    response = (
        service_client.table("photo_submissions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )

    submissions = response.data or []

    if not submissions:
        st.info("No submissions yet.")
        return

    status_filter = st.selectbox(
        "Filter by status",
        options=["All", "pending", "approved", "private", "rejected"],
        index=0,
    )

    if status_filter != "All":
        submissions = [
            row for row in submissions if row["status"] == status_filter
        ]

    st.markdown(f"### {len(submissions)} submission(s)")

    for index, row in enumerate(submissions):
        with st.container(border=True):
            col_photo, col_details = st.columns([1, 2])

            with col_photo:
                try:
                    signed = service_client.storage.from_(bucket).create_signed_url(
                        row["photo_path"],
                        60 * 60,
                    )
                    image_url = signed.get("signedURL") or signed.get("signedUrl")

                    if image_url:
                        st.image(image_url, use_container_width=True)
                    else:
                        st.warning("Could not create a photo preview.")
                except Exception:
                    st.warning("Photo preview unavailable.")

                st.download_button(
                    label="Download photo",
                    data=service_client.storage.from_(bucket).download(row["photo_path"]),
                    file_name=os.path.basename(row["photo_path"]),
                    mime="application/octet-stream",
                    key=f"download-{row['id']}",
                )

            with col_details:
                st.markdown(f"**Submitted by:** {row['submitter_name']}")
                if row.get("submitter_relationship"):
                    st.markdown(f"**Relationship:** {row['submitter_relationship']}")
                if row.get("submitter_contact"):
                    st.markdown(f"**Contact:** {row['submitter_contact']}")

                st.markdown(f"**People in photo:** {row['people_in_photo']}")
                if row.get("date_taken"):
                    st.markdown(f"**When taken:** {row['date_taken']}")
                if row.get("location"):
                    st.markdown(f"**Where taken:** {row['location']}")
                if row.get("memory"):
                    st.markdown(f"**Memory:** {row['memory']}")

                st.markdown(f"**Display permission:** {row['display_permission']}")
                st.markdown(f"**Current status:** `{row['status']}`")
                st.markdown(f"**Submitted:** {row['created_at']}")

                new_status = st.selectbox(
                    "Set status",
                    options=["pending", "approved", "private", "rejected"],
                    index=["pending", "approved", "private", "rejected"].index(row["status"]),
                    key=f"status-{row['id']}",
                )

                display_order = st.number_input(
                    "Display order",
                    min_value=0,
                    value=row.get("display_order") or 0,
                    key=f"order-{row['id']}",
                )

                curator_notes = st.text_area(
                    "Curator notes",
                    value=row.get("curator_notes") or "",
                    height=80,
                    key=f"notes-{row['id']}",
                )

                if st.button("Save changes", key=f"save-{row['id']}"):
                    service_client.table("photo_submissions").update(
                        {
                            "status": new_status,
                            "display_order": int(display_order),
                            "curator_notes": curator_notes,
                        }
                    ).eq("id", row["id"]).execute()
                    st.success("Saved.")
                    st.rerun()

                st.markdown("#### Suggested wake caption")
                st.code(make_caption(row), language="text")


def export_approved_photos():
    service_client = get_service_client()

    response = (
        service_client.table("photo_submissions")
        .select("*")
        .eq("status", "approved")
        .order("display_order", desc=False)
        .execute()
    )

    rows = response.data or []

    if not rows:
        st.info("No approved photos to export yet.")
        return

    export_rows = []

    for row in rows:
        export_rows.append(
            {
                "display_order": row.get("display_order") or 0,
                "photo_path": row["photo_path"],
                "photo_url": row.get("photo_url") or "",
                "submitter_name": row["submitter_name"],
                "people_in_photo": row["people_in_photo"],
                "date_taken": row.get("date_taken") or "",
                "location": row.get("location") or "",
                "memory": row.get("memory") or "",
                "caption": make_caption(row),
            }
        )

    df = pd.DataFrame(export_rows)

    csv_bytes = df.to_csv(index=False).encode("utf-8")

    st.download_button(
        label="Download approved captions CSV",
        data=csv_bytes,
        file_name="wake_photo_captions.csv",
        mime="text/csv",
    )

    st.dataframe(df, use_container_width=True)


def main():
    page = st.sidebar.radio(
        "Choose an area",
        options=["Upload a photo", "Curator dashboard"],
    )

    if page == "Upload a photo":
        render_public_upload()
    else:
        if curator_login():
            render_curator_dashboard()

            st.divider()
            export_approved_photos()


if __name__ == "__main__":
    main()
