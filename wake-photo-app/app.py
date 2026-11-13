import io
import os
import re
import uuid
from datetime import date, datetime

import pandas as pd
import streamlit as st
from PIL import Image
from pillow_heif import register_heif_opener
from supabase import create_client, Client

register_heif_opener()

st.set_page_config(
    page_title="In Loving Memory Of John Francis Lismore",
    page_icon="🤍",
    layout="wide",
)

ALLOWED_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "application/octet-stream",
}

MAX_PHOTOS = 5
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
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in base
    ).lower()
    return cleaned or "photo.jpg"


def format_date(value):
    if not value:
        return ""

    if isinstance(value, str):
        return value

    return value.strftime("%d %B %Y")


def convert_to_jpeg(uploaded_file):
    """Convert HEIC or HEIF images to JPEG before uploading to Supabase."""

    filename = uploaded_file.name.lower()

    if not filename.endswith((".heic", ".heif")):
        return (
            uploaded_file.getvalue(),
            uploaded_file.type,
            uploaded_file.name,
        )

    image = Image.open(uploaded_file)

    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")

    jpeg_buffer = io.BytesIO()

    image.save(
        jpeg_buffer,
        format="JPEG",
        quality=90,
        optimize=True,
    )

    jpeg_filename = (
        os.path.splitext(uploaded_file.name)[0] + ".jpg"
    )

    return (
        jpeg_buffer.getvalue(),
        "image/jpeg",
        jpeg_filename,
    )


def make_caption(submission):
    if submission.get("date_type") == "exact":
        date_text = format_date(submission.get("exact_date"))
    else:
        date_text = submission.get("estimated_date") or ""

    location = submission.get("location") or ""
    postcode = submission.get("postcode") or ""

    location_text = location

    if postcode:
        location_text = f"{location_text}, {postcode}".strip(", ")

    context_parts = [
        part for part in [date_text, location_text] if part
    ]
    context = " — ".join(context_parts)

    caption = submission.get("people_in_photo") or ""

    if context:
        caption += f"\n{context}"

    memory = submission.get("memory")

    if memory:
        caption += f'\n“{memory}”'

    submitter = submission.get("submitter_name")

    if submitter:
        caption += f"\nShared by {submitter}"

    return caption


def get_photo_sort_year(submission):
    """Return a sortable year for a submission."""

    if (
        submission.get("date_type") == "exact"
        and submission.get("exact_date")
    ):
        try:
            return int(
                str(submission["exact_date"])[:4]
            )
        except (TypeError, ValueError):
            pass

    estimated_date = (
        submission.get("estimated_date") or ""
    ).lower()

    year_match = re.search(
        r"\b(18\d{2}|19\d{2}|20\d{2})\b",
        estimated_date,
    )

    if year_match:
        return int(year_match.group(1))

    decade_match = re.search(
        r"\b(18|19|20)\d0s\b",
        estimated_date,
    )

    if decade_match:
        return int(decade_match.group(0)[:4])

    return 9999


def render_public_upload():
    st.markdown(
        """
        <div style="
            text-align: center;
            padding: 2rem 1.5rem;
            background-color: #F1ECE4;
            border-radius: 16px;
            margin-bottom: 2rem;
        ">
            <h1 style="
                margin: 0 0 0.5rem 0;
                color: #33302B;
                font-weight: 600;
            ">
                In Loving Memory Of John Francis Lismore
            </h1>
            <p style="
                margin: 0;
                color: #5C554B;
                font-size: 1.05rem;
            ">
                Sharing photographs and memories
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        We are gathering photographs and memories of John to share at his wake
        and to keep as a lasting family collection.

        Please add any photographs you would like to share — from family
        occasions, holidays, work, friendships, sport, or everyday moments.
        Photographs we may never have seen are especially welcome.

        Approximate dates and partial information are completely fine.
        """
    )

    if "submission_complete" not in st.session_state:
        st.session_state.submission_complete = False

    if st.session_state.submission_complete:
        st.success(
            "Thank you — your photographs and memory have been received."
        )

        st.markdown(
            """
            Your contribution will help us create a collection of photographs
            and memories to share at John’s wake, and to keep for the family
            afterwards.
            """
        )

        if st.button(
            "Click here to share photographs from another memory",
            use_container_width=True,
        ):
            st.session_state.submission_complete = False
            st.rerun()

        st.info(
            "Thank you again for helping to create this collection of "
            "photographs and memories."
        )

        return

    with st.form("memory_submission", clear_on_submit=True):
        with st.container(border=True):
            st.subheader("About you")

            submitter_name = st.text_input(
                "Your name *",
                placeholder="For example: Sarah Smith",
            )

            submitter_relationship = st.text_input(
                "Your relationship to John",
                placeholder=(
                    "For example: daughter, son, friend, colleague"
                ),
            )

        st.markdown("")

        with st.container(border=True):
            st.subheader("The memory")

            people_in_photo = st.text_area(
                "Who is in the photo, and what was the occasion? *",
                placeholder=(
                    "For example: John with Mum, Sarah and James at "
                    "Christmas 1998."
                ),
                height=110,
            )

            memory = st.text_area(
                "Memory or story",
                placeholder=(
                    "Please share anything you remember — a funny moment, "
                    "what John was like, why the photo matters, or a message "
                    "you would like included."
                ),
                height=160,
            )

        st.markdown("")

        with st.container(border=True):
            st.subheader("When and where")

            st.markdown(
                "Please provide either an exact date or an approximate date. "
                "You only need to complete one of these."
            )

            col_exact_date, col_estimated_date = st.columns(2)

            with col_exact_date:
                exact_date = st.date_input(
                    "Exact date",
                    value=None,
                    min_value=date(1900, 1, 1),
                    max_value=date.today(),
                    format="DD/MM/YYYY",
                    help=(
                        "Optional. Choose the exact date from the calendar."
                    ),
                )

            with col_estimated_date:
                estimated_date = st.text_input(
                    "Exact date unknown — approximate date or period",
                    placeholder=(
                        "For example: Summer 1970, the 1980s, "
                        "around my wedding"
                    ),
                    help=(
                        "Optional. Use this if you do not know the exact date."
                    ),
                )

            col_location, col_postcode = st.columns(2)

            with col_location:
                location = st.text_input(
                    "Where was it taken?",
                    placeholder=(
                        "For example: home, Cornwall, wedding venue"
                    ),
                )

            with col_postcode:
                postcode = st.text_input(
                    "Postcode or ZIP code",
                    help=(
                        "Optional. This can help us map the photos later."
                    ),
                    placeholder="For example: BR6 or SW1A 1AA",
                )

        st.markdown("")

        with st.container(border=True):
            st.subheader("Your photographs")

            st.markdown(
                """
                Please upload up to five photographs connected to this memory.

                They can be formal or candid, recent or many years old. If you
                have several photographs from the same occasion, you can add
                them together. If you have further memories, you can select to
                share further memories below.
                """
            )

            uploaded_files = st.file_uploader(
                "Drag and drop photographs here, or click to choose files",
                type=[
                    "jpg",
                    "jpeg",
                    "png",
                    "webp",
                    "heic",
                    "heif",
                ],
                accept_multiple_files=True,
            )

            if uploaded_files:
                st.info(
                    f"{len(uploaded_files)} photograph(s) selected."
                )

                preview_columns = st.columns(
                    min(len(uploaded_files), 3)
                )

                for index, uploaded_file in enumerate(
                    uploaded_files[:6]
                ):
                    with preview_columns[index % 3]:
                        try:
                            st.image(
                                uploaded_file,
                                use_container_width=True,
                            )
                        except Exception:
                            st.caption(
                                uploaded_file.name
                            )

        st.markdown("")

        submitted = st.form_submit_button(
            "Share these photographs and memories",
            use_container_width=True,
        )

    if not submitted:
        return

    errors = []

    if not submitter_name.strip():
        errors.append("Please enter your name.")

    if not people_in_photo.strip():
        errors.append(
            "Please say who is in the photo or what the occasion was."
        )

    if not uploaded_files:
        errors.append(
            "Please choose at least one photograph."
        )

    if uploaded_files and len(uploaded_files) > MAX_PHOTOS:
        errors.append(
            f"Please upload no more than {MAX_PHOTOS} photographs "
            "per memory."
        )

    if exact_date is None and not estimated_date.strip():
        errors.append(
            "Please provide either an exact date or an approximate "
            "date or period."
        )

    if uploaded_files:
        for uploaded_file in uploaded_files:
            if uploaded_file.type not in ALLOWED_TYPES:
                errors.append(
                    f"{uploaded_file.name} is not a supported image type. "
                    "Please use JPG, JPEG, PNG, WEBP, HEIC or HEIF."
                )

            if uploaded_file.size > MAX_FILE_MB * 1024 * 1024:
                errors.append(
                    f"{uploaded_file.name} is larger than "
                    f"{MAX_FILE_MB} MB."
                )

    if errors:
        for error in errors:
            st.error(error)

        return

    submission_id = str(uuid.uuid4())
    public_client = get_public_client()
    bucket = st.secrets["SUPABASE_BUCKET"]

    submission_record = {
        "id": submission_id,
        "submitter_name": submitter_name.strip(),
        "submitter_relationship": (
            submitter_relationship.strip() or None
        ),
        "people_in_photo": people_in_photo.strip(),
        "date_type": (
            "exact"
            if exact_date is not None
            else "estimated"
        ),
        "exact_date": (
            exact_date.isoformat() if exact_date else None
        ),
        "estimated_date": estimated_date.strip() or None,
        "location": location.strip() or None,
        "postcode": postcode.strip() or None,
        "memory": memory.strip() or None,
        "status": "pending",
    }

    uploaded_photo_records = []

    try:
        with st.spinner("Sharing your photographs..."):
            for photo_order, uploaded_file in enumerate(
                uploaded_files,
                start=1,
            ):
                (
                    file_bytes,
                    content_type,
                    original_filename,
                ) = convert_to_jpeg(uploaded_file)

                filename = safe_filename(original_filename)
                storage_path = (
                    f"pending/{submission_id}/{filename}"
                )

                public_client.storage.from_(bucket).upload(
                    file=file_bytes,
                    path=storage_path,
                    file_options={
                        "content-type": content_type,
                        "cache-control": "3600",
                        "upsert": "false",
                    },
                )

                uploaded_photo_records.append(
                    {
                        "submission_id": submission_id,
                        "storage_path": storage_path,
                        "original_filename": original_filename,
                        "photo_order": photo_order,
                    }
                )

        public_client.table("submissions").insert(
            submission_record,
            returning="minimal",
        ).execute()

        public_client.table("submission_photos").insert(
            uploaded_photo_records,
            returning="minimal",
        ).execute()

        st.session_state.submission_complete = True
        st.rerun()

    except Exception as error:
        st.error(
            "Sorry, we could not save your submission. "
            "Please try again, or email the photographs to the family."
        )

        error_details = getattr(error, "args", None)

        if error_details:
            st.error(
                f"Technical details: {error_details}"
            )
        else:
            st.error(
                f"Technical details: {error}"
            )


def curator_login() -> bool:
    st.title("🔐 Curator dashboard")

    password = st.text_input(
        "Curator password",
        type="password",
    )

    if not password:
        st.info(
            "Enter the curator password to continue."
        )
        return False

    if password != st.secrets["CURATOR_PASSWORD"]:
        st.error("Incorrect password.")
        return False

    return True


def load_curator_data():
    service_client = get_service_client()

    submissions_response = (
        service_client.table("submissions")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )

    photos_response = (
        service_client.table("submission_photos")
        .select("*")
        .order("photo_order", desc=False)
        .execute()
    )

    submissions = submissions_response.data or []
    photos = photos_response.data or []

    photos_by_submission = {}

    for photo in photos:
        photos_by_submission.setdefault(
            photo["submission_id"],
            [],
        ).append(photo)

    for submission in submissions:
        submission["photos"] = photos_by_submission.get(
            submission["id"],
            [],
        )

    return service_client, submissions


def render_timeline(submissions):
    st.subheader("Upload timeline")

    if not submissions:
        st.info(
            "No submissions have been received yet."
        )
        return

    submissions_by_day = {}

    for submission in submissions:
        submitted_day = submission["created_at"][:10]
        submissions_by_day.setdefault(
            submitted_day,
            [],
        ).append(submission)

    for day in sorted(
        submissions_by_day.keys(),
        reverse=True,
    ):
        day_submissions = submissions_by_day[day]

        with st.container(border=True):
            st.markdown(
                f"### "
                f"{datetime.strptime(day, '%Y-%m-%d').strftime('%d %B %Y')}"
            )
            st.markdown(
                f"**{len(day_submissions)} submission(s)**"
            )

            for submission in day_submissions:
                photo_count = len(
                    submission.get("photos", [])
                )

                with st.expander(
                    f"{submission['submitter_name']} — "
                    f"{photo_count} photograph(s) — "
                    f"{submission['status'].title()}"
                ):
                    st.markdown(
                        f"**Submitted at:** {submission['created_at']}"
                    )
                    st.markdown(
                        f"**Shared by:** {submission['submitter_name']}"
                    )

                    if submission.get(
                        "submitter_relationship"
                    ):
                        st.markdown(
                            "**Relationship:** "
                            f"{submission['submitter_relationship']}"
                        )

                    st.markdown(
                        "**People / occasion:** "
                        f"{submission['people_in_photo']}"
                    )

                    if submission["date_type"] == "exact":
                        st.markdown(
                            "**Exact date:** "
                            f"{format_date(submission.get('exact_date'))}"
                        )
                    else:
                        st.markdown(
                            "**Approximate date:** "
                            f"{submission.get('estimated_date') or 'Not provided'}"
                        )

                    if submission.get("location"):
                        st.markdown(
                            f"**Location:** {submission['location']}"
                        )

                    if submission.get("postcode"):
                        st.markdown(
                            "**Postcode / ZIP:** "
                            f"{submission['postcode']}"
                        )

                    if submission.get("memory"):
                        st.markdown(
                            f"**Memory:** {submission['memory']}"
                        )

                    st.markdown(
                        f"**Photographs:** {photo_count}"
                    )


def render_gallery(service_client, submissions):
    st.subheader("Photo gallery")

    st.markdown(
        """
        Photographs are shown in time order, from the earliest date to the
        most recent. Select a memory to see all of its photographs and details.
        """
    )

    gallery_submissions = [
        submission
        for submission in submissions
        if submission.get("status") == "approved"
        and submission.get("photos")
    ]

    if not gallery_submissions:
        st.info(
            "No approved photographs are available for the gallery yet. "
            "Approve submissions in the **Review and edit** tab."
        )
        return

    gallery_submissions.sort(
        key=get_photo_sort_year,
    )

    if "selected_gallery_submission" not in st.session_state:
        st.session_state.selected_gallery_submission = None

    selected_id = st.session_state.selected_gallery_submission

    selected_submission = next(
        (
            submission
            for submission in gallery_submissions
            if submission["id"] == selected_id
        ),
        None,
    )

    if selected_submission:
        render_gallery_detail(
            service_client,
            selected_submission,
        )
        return

    st.markdown(
        f"### {len(gallery_submissions)} memories"
    )

    gallery_columns = st.columns(
        3,
        gap="small",
    )

    for index, submission in enumerate(
        gallery_submissions
    ):
        photos = submission.get(
            "photos",
            [],
        )

        if not photos:
            continue

        first_photo = photos[0]

        column = gallery_columns[
            index % 3
        ]

        with column:
            try:
                signed = service_client.storage.from_(
                    st.secrets[
                        "SUPABASE_BUCKET"
                    ]
                ).create_signed_url(
                    first_photo[
                        "storage_path"
                    ],
                    60 * 60,
                )

                image_url = signed.get(
                    "signedURL"
                ) or signed.get(
                    "signedUrl"
                )

                if not image_url:
                    image_url = ""
            except Exception:
                image_url = ""

            if image_url:
                st.markdown(
                    f"""
                    <div style="
                        aspect-ratio: 1 / 1;
                        overflow: hidden;
                        border-radius: 12px;
                        margin-bottom: 0.5rem;
                        background-color: #F1ECE4;
                    ">
                        <img
                            src="{image_url}"
                            alt="Photograph from a shared memory"
                            style="
                                width: 100%;
                                height: 100%;
                                object-fit: cover;
                                object-position: center;
                                display: block;
                            "
                        />
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.warning(
                    "Preview unavailable."
                )

            if submission.get(
                "date_type"
            ) == "exact":
                date_text = format_date(
                    submission.get(
                        "exact_date"
                    )
                )
            else:
                date_text = (
                    submission.get(
                        "estimated_date"
                    )
                    or "Date unknown"
                )

            st.caption(
                f"**{date_text}**"
            )

            st.caption(
                submission.get(
                    "people_in_photo"
                )
                or ""
            )

            if st.button(
                "View photographs and memory",
                key=f"gallery-{submission['id']}",
                use_container_width=True,
            ):
                st.session_state.selected_gallery_submission = (
                    submission["id"]
                )
                st.rerun()


def render_gallery_detail(
    service_client,
    submission,
):
    if st.button(
        "← Back to gallery",
    ):
        st.session_state.selected_gallery_submission = None
        st.rerun()

    st.title(
        format_date(
            submission.get("exact_date")
        )
        if submission.get("date_type") == "exact"
        else (
            submission.get("estimated_date")
            or "Date unknown"
        )
    )

    st.markdown(
        f"**People / occasion:** "
        f"{submission.get('people_in_photo') or 'Not provided'}"
    )

    if submission.get("location"):
        st.markdown(
            f"**Location:** {submission['location']}"
        )

    if submission.get("postcode"):
        st.markdown(
            f"**Postcode / ZIP:** "
            f"{submission['postcode']}"
        )

    if submission.get("memory"):
        st.markdown(
            f"**Memory:** {submission['memory']}"
        )

    st.markdown(
        f"**Shared by:** "
        f"{submission.get('submitter_name') or 'Unknown'}"
    )

    st.divider()

    photos = submission.get(
        "photos",
        [],
    )

    if not photos:
        st.info(
            "No photographs are attached to this submission."
        )
        return

    st.markdown(
        f"### {len(photos)} photograph(s)"
    )

    photo_columns = st.columns(
        min(len(photos), 2)
    )

    for index, photo in enumerate(photos):
        with photo_columns[
            index % 2
        ]:
            try:
                signed = service_client.storage.from_(
                    st.secrets[
                        "SUPABASE_BUCKET"
                    ]
                ).create_signed_url(
                    photo["storage_path"],
                    60 * 60,
                )

                image_url = signed.get(
                    "signedURL"
                ) or signed.get(
                    "signedUrl"
                )

                if image_url:
                    st.image(
                        image_url,
                        use_container_width=True,
                    )
                else:
                    st.warning(
                        "Preview unavailable."
                    )
            except Exception:
                st.warning(
                    "Preview unavailable."
                )

            st.caption(
                f"Photograph {photo['photo_order']} "
                f"of {len(photos)}"
            )

    st.divider()

    st.markdown(
        "#### Suggested wake caption"
    )
    st.code(
        make_caption(submission),
        language="text",
    )


def render_curator_dashboard():
    st.title("📋 Photo submissions")

    service_client, submissions = load_curator_data()
    bucket = st.secrets["SUPABASE_BUCKET"]

    tab_timeline, tab_gallery, tab_review, tab_export = st.tabs(
        [
            "Timeline",
            "Gallery",
            "Review and edit",
            "Export",
        ]
    )

    with tab_timeline:
        render_timeline(submissions)

    with tab_gallery:
        render_gallery(
            service_client,
            submissions,
        )

    with tab_review:
        status_filter = st.selectbox(
            "Filter by status",
            options=[
                "All",
                "pending",
                "approved",
                "private",
                "rejected",
            ],
            index=0,
        )

        filtered = submissions

        if status_filter != "All":
            filtered = [
                submission
                for submission in submissions
                if submission["status"] == status_filter
            ]

        st.markdown(
            f"### {len(filtered)} submission(s)"
        )

        if not filtered:
            st.info(
                "No submissions match this filter."
            )

        for submission in filtered:
            with st.container(border=True):
                st.markdown(
                    f"#### Shared by {submission['submitter_name']}"
                )
                st.markdown(
                    f"**Submitted:** {submission['created_at']}"
                )
                st.markdown(
                    "**People / occasion:** "
                    f"{submission['people_in_photo']}"
                )

                if submission["date_type"] == "exact":
                    st.markdown(
                        "**Exact date:** "
                        f"{format_date(submission.get('exact_date'))}"
                    )
                else:
                    st.markdown(
                        "**Approximate date:** "
                        f"{submission.get('estimated_date') or 'Not provided'}"
                    )

                if submission.get("location"):
                    st.markdown(
                        f"**Location:** {submission['location']}"
                    )

                if submission.get("postcode"):
                    st.markdown(
                        "**Postcode / ZIP:** "
                        f"{submission['postcode']}"
                    )

                if submission.get("memory"):
                    st.markdown(
                        f"**Memory:** {submission['memory']}"
                    )

                st.markdown(
                    f"**Current status:** `{submission['status']}`"
                )

                photos = submission.get(
                    "photos",
                    [],
                )

                if photos:
                    photo_columns = st.columns(
                        min(len(photos), 3)
                    )

                    for index, photo in enumerate(
                        photos
                    ):
                        with photo_columns[
                            index % 3
                        ]:
                            try:
                                signed = (
                                    service_client.storage.from_(
                                        bucket
                                    ).create_signed_url(
                                        photo[
                                            "storage_path"
                                        ],
                                        60 * 60,
                                    )
                                )

                                image_url = signed.get(
                                    "signedURL"
                                ) or signed.get(
                                    "signedUrl"
                                )

                                if image_url:
                                    st.image(
                                        image_url,
                                        use_container_width=True,
                                    )
                                else:
                                    st.warning(
                                        "Preview unavailable."
                                    )
                            except Exception:
                                st.warning(
                                    "Preview unavailable."
                                )

                            st.caption(
                                f"Photograph {photo['photo_order']}: "
                                f"{photo['original_filename']}"
                            )

                new_status = st.selectbox(
                    "Set submission status",
                    options=[
                        "pending",
                        "approved",
                        "private",
                        "rejected",
                    ],
                    index=[
                        "pending",
                        "approved",
                        "private",
                        "rejected",
                    ].index(
                        submission["status"]
                    ),
                    key=f"status-{submission['id']}",
                )

                display_order = st.number_input(
                    "Display order",
                    min_value=0,
                    value=submission.get(
                        "display_order"
                    )
                    or 0,
                    key=f"order-{submission['id']}",
                )

                curator_notes = st.text_area(
                    "Curator notes",
                    value=submission.get(
                        "curator_notes"
                    )
                    or "",
                    height=90,
                    key=f"notes-{submission['id']}",
                )

                if st.button(
                    "Save changes",
                    key=f"save-{submission['id']}",
                ):
                    service_client.table(
                        "submissions"
                    ).update(
                        {
                            "status": new_status,
                            "display_order": int(
                                display_order
                            ),
                            "curator_notes": curator_notes,
                        }
                    ).eq(
                        "id",
                        submission["id"],
                    ).execute()

                    st.success("Saved.")
                    st.rerun()

                st.markdown(
                    "#### Suggested wake caption"
                )
                st.code(
                    make_caption(submission),
                    language="text",
                )

    with tab_export:
        st.subheader(
            "Export for Google Sheets"
        )

        export_rows = []

        for submission in submissions:
            photos = submission.get(
                "photos",
                [],
            )

            if not photos:
                export_rows.append(
                    {
                        "submission_created_at": submission[
                            "created_at"
                        ],
                        "submitter_name": submission[
                            "submitter_name"
                        ],
                        "submitter_relationship": submission.get(
                            "submitter_relationship"
                        )
                        or "",
                        "people_in_photo": submission[
                            "people_in_photo"
                        ],
                        "date_type": submission[
                            "date_type"
                        ],
                        "exact_date": format_date(
                            submission.get(
                                "exact_date"
                            )
                        ),
                        "estimated_date": submission.get(
                            "estimated_date"
                        )
                        or "",
                        "location": submission.get(
                            "location"
                        )
                        or "",
                        "postcode": submission.get(
                            "postcode"
                        )
                        or "",
                        "memory": submission.get(
                            "memory"
                        )
                        or "",
                        "photo_filename": "",
                        "photo_storage_path": "",
                        "photo_order": "",
                        "submission_status": submission[
                            "status"
                        ],
                        "display_order": submission.get(
                            "display_order"
                        )
                        or 0,
                        "curator_notes": submission.get(
                            "curator_notes"
                        )
                        or "",
                        "caption": make_caption(
                            submission
                        ),
                    }
                )

            for photo in photos:
                export_rows.append(
                    {
                        "submission_created_at": submission[
                            "created_at"
                        ],
                        "submitter_name": submission[
                            "submitter_name"
                        ],
                        "submitter_relationship": submission.get(
                            "submitter_relationship"
                        )
                        or "",
                        "people_in_photo": submission[
                            "people_in_photo"
                        ],
                        "date_type": submission[
                            "date_type"
                        ],
                        "exact_date": format_date(
                            submission.get(
                                "exact_date"
                            )
                        ),
                        "estimated_date": submission.get(
                            "estimated_date"
                        )
                        or "",
                        "location": submission.get(
                            "location"
                        )
                        or "",
                        "postcode": submission.get(
                            "postcode"
                        )
                        or "",
                        "memory": submission.get(
                            "memory"
                        )
                        or "",
                        "photo_filename": photo[
                            "original_filename"
                        ],
                        "photo_storage_path": photo[
                            "storage_path"
                        ],
                        "photo_order": photo[
                            "photo_order"
                        ],
                        "submission_status": submission[
                            "status"
                        ],
                        "display_order": submission.get(
                            "display_order"
                        )
                        or 0,
                        "curator_notes": submission.get(
                            "curator_notes"
                        )
                        or "",
                        "caption": make_caption(
                            submission
                        ),
                    }
                )

        if not export_rows:
            st.info(
                "No submissions available to export yet."
            )
            return

        df = pd.DataFrame(export_rows)

        csv_bytes = df.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            label="Download CSV for Google Sheets",
            data=csv_bytes,
            file_name="wake_photo_submissions.csv",
            mime="text/csv",
            use_container_width=True,
        )

        st.markdown(
            """
            ### Import into Google Sheets

            1. Download the CSV file.
            2. Open a new Google Sheet.
            3. Choose **File → Import**.
            4. Select **Upload** and choose the downloaded CSV file.
            5. Choose **Replace spreadsheet** or **Insert new sheet(s)**.
            6. Click **Import data**.

            Each row represents one photograph. If a memory has five
            photographs, it will appear across five rows, with the shared
            memory details repeated.
            """
        )

        st.dataframe(
            df,
            use_container_width=True,
        )


def main():
    page = st.sidebar.radio(
        "Choose an area",
        options=[
            "Share a photograph",
            "Curator dashboard",
        ],
    )

    if page == "Share a photograph":
        render_public_upload()
    else:
        if curator_login():
            render_curator_dashboard()


if __name__ == "__main__":
    main()
