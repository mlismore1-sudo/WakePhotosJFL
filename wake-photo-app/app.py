import io
import os
import re
import smtplib
import uuid
from datetime import datetime
from email.message import EmailMessage

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

DECADES = [
    "1940s",
    "1950s",
    "1960s",
    "1970s",
    "1980s",
    "1990s",
    "2000s",
    "2010s",
    "2020s",
]


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


def prepare_image_for_upload(
    uploaded_file,
    rotation_degrees,
):
    """Return image bytes for upload, applying rotation and HEIC conversion."""

    file_bytes = uploaded_file.getvalue()
    filename = uploaded_file.name.lower()

    if (
        rotation_degrees == 0
        and not filename.endswith(
            (".heic", ".heif")
        )
    ):
        return (
            file_bytes,
            uploaded_file.type,
            uploaded_file.name,
        )

    image = Image.open(
        io.BytesIO(file_bytes)
    )

    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")

    if rotation_degrees:
        image = image.rotate(
            rotation_degrees,
            expand=True,
        )

    jpeg_buffer = io.BytesIO()

    image.save(
        jpeg_buffer,
        format="JPEG",
        quality=90,
        optimize=True,
    )

    jpeg_filename = (
        os.path.splitext(uploaded_file.name)[0]
        + ".jpg"
    )

    return (
        jpeg_buffer.getvalue(),
        "image/jpeg",
        jpeg_filename,
    )


def get_rotated_preview(
    uploaded_file,
    rotation_degrees,
):
    """Return a rotated preview image for display in Streamlit."""

    image = Image.open(uploaded_file)

    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")

    if rotation_degrees:
        image = image.rotate(
            rotation_degrees,
            expand=True,
        )

    return image


def send_confirmation_email(
    email_address,
    submitter_name,
):
    """Send an optional confirmation email if SMTP settings are available."""

    try:
        smtp_host = st.secrets["SMTP_HOST"]
        smtp_port = int(
            st.secrets["SMTP_PORT"]
        )
        smtp_user = st.secrets["SMTP_USER"]
        smtp_password = st.secrets[
            "SMTP_PASSWORD"
        ]
        from_email = st.secrets["FROM_EMAIL"]
    except KeyError:
        return False

    try:
        message = EmailMessage()

        message["Subject"] = (
            "Thank you for sharing photographs"
        )

        message["From"] = from_email
        message["To"] = email_address

        message.set_content(
            f"""
Hi {submitter_name},

Thank you for sharing photographs for John’s memory collection.

Your photographs have been received and will be reviewed by the family
before being added to the shared gallery.

If you would like to share more photographs, you can return to the
submission page at any time.

With thanks,
The family of John Francis Lismore
            """
        )

        with smtplib.SMTP(
            smtp_host,
            smtp_port,
        ) as server:
            server.starttls()
            server.login(
                smtp_user,
                smtp_password,
            )
            server.send_message(message)

        return True

    except Exception:
        return False


def initialise_submission_state():
    if "submission_step" not in st.session_state:
        st.session_state.submission_step = 1

    if "submission_complete" not in st.session_state:
        st.session_state.submission_complete = False

    if "photo_id_map" not in st.session_state:
        st.session_state.photo_id_map = {}

    if "photo_keys" not in st.session_state:
        st.session_state.photo_keys = []

    if "removed_photo_ids" not in st.session_state:
        st.session_state.removed_photo_ids = set()

    if "photo_files" not in st.session_state:
        st.session_state.photo_files = {}

    if "photo_rotations" not in st.session_state:
        st.session_state.photo_rotations = {}

    if "apply_caption_to_all" not in st.session_state:
        st.session_state.apply_caption_to_all = False


def sync_photo_state(uploaded_files):
    """Keep photo order, removals, rotations and captions in session state."""

    state = st.session_state

    current_pairs = [
        (index, uploaded_file.name)
        for index, uploaded_file in enumerate(
            uploaded_files
        )
    ]

    current_pair_set = set(current_pairs)

    for pair in list(
        state.photo_id_map.keys()
    ):
        if pair not in current_pair_set:
            photo_id = state.photo_id_map.pop(
                pair
            )

            if photo_id in state.photo_keys:
                state.photo_keys.remove(
                    photo_id
                )

            state.photo_files.pop(
                photo_id,
                None,
            )

            state.photo_rotations.pop(
                photo_id,
                None,
            )

            state.removed_photo_ids.discard(
                photo_id
            )

    for pair in current_pairs:
        if pair not in state.photo_id_map:
            photo_id = str(uuid.uuid4())

            state.photo_id_map[pair] = (
                photo_id
            )

            state.photo_keys.append(photo_id)

    ordered_photos = []

    for photo_id in state.photo_keys:
        pair = next(
            (
                pair
                for pair, value in state.photo_id_map.items()
                if value == photo_id
            ),
            None,
        )

        if pair is None:
            continue

        if photo_id in state.removed_photo_ids:
            continue

        index, filename = pair
        uploaded_file = uploaded_files[index]

        state.photo_files[photo_id] = (
            uploaded_file
        )

        ordered_photos.append(
            (
                photo_id,
                uploaded_file,
            )
        )

    state.photo_keys = [
        photo_id
        for photo_id, uploaded_file in ordered_photos
    ]

    return ordered_photos


def render_step_indicator(current_step):
    steps = [
        "Your details",
        "Add photographs",
        "Check and share",
    ]

    progress_value = (
        current_step - 1
    ) / len(steps)

    st.progress(
        progress_value,
        text=f"Step {current_step} of {len(steps)} — {steps[current_step - 1]}",
    )


def render_step_one():
    st.subheader("Your details")

    st.text_input(
        "Your name *",
        key="submitter_name",
        placeholder="For example: Sarah Smith",
    )

    st.text_input(
        "Your email address",
        key="submitter_email",
        placeholder="For example: name@example.com",
        help=(
            "Optional. If you provide an email address, we can send you a "
            "short confirmation after your photographs have been received."
        ),
    )

    st.info(
        "Your photographs will be reviewed by the family before appearing "
        "in the shared gallery."
    )

    st.markdown("")

    if st.button(
        "Continue to photographs →",
        use_container_width=True,
    ):
        if not st.session_state.submitter_name.strip():
            st.error(
                "Please enter your name before continuing."
            )
            return

        st.session_state.submission_step = 2
        st.rerun()

    if st.button(
        "← Back",
        use_container_width=True,
    ):
        st.session_state.submission_step = 1
        st.rerun()


def render_step_two(uploaded_files):
    st.subheader("Add your photographs")

    st.markdown(
        """
        Choose up to five photographs. You can add a caption, rotate a
        photograph, or change its order before sharing.
        """
    )

    uploaded_files = st.file_uploader(
        "Choose photographs",
        type=[
            "jpg",
            "jpeg",
            "png",
            "webp",
            "heic",
            "heif",
        ],
        accept_multiple_files=True,
        key="photo_uploader",
    )

    if uploaded_files:
        st.info(
            f"{len(uploaded_files)} photograph(s) selected."
        )

        if len(uploaded_files) > MAX_PHOTOS:
            st.error(
                f"Please upload no more than {MAX_PHOTOS} photographs."
            )

        st.markdown(
            "### Captions"
        )

        st.markdown(
            """
            Add a caption for each photograph. You can also use the same
            caption for every photograph, then edit individual captions if
            needed.
            """
        )

        master_caption = st.text_input(
            "Caption to apply to all photographs",
            key="master_caption",
            placeholder=(
                "For example: John with family at Christmas"
            ),
        )

        if st.button(
            "Apply this caption to all photographs",
            use_container_width=True,
        ):
            st.session_state.apply_caption_to_all = True
            st.rerun()

        if st.session_state.apply_caption_to_all:
            for photo_id, uploaded_file in sync_photo_state(
                uploaded_files
            ):
                caption_key = (
                    f"caption-{photo_id}"
                )

                st.session_state[caption_key] = (
                    st.session_state.master_caption
                )

            st.session_state.apply_caption_to_all = False

        ordered_photos = sync_photo_state(
            uploaded_files
        )

        for position, (
            photo_id,
            uploaded_file,
        ) in enumerate(
            ordered_photos
        ):
            rotation_degrees = (
                st.session_state.photo_rotations.get(
                    photo_id,
                    0,
                )
            )

            with st.container(border=True):
                photo_columns = st.columns(
                    [1, 2]
                )

                with photo_columns[0]:
                    try:
                        rotated_preview = (
                            get_rotated_preview(
                                uploaded_file,
                                rotation_degrees,
                            )
                        )

                        st.image(
                            rotated_preview,
                            use_container_width=True,
                        )
                    except Exception:
                        st.caption(
                            uploaded_file.name
                        )

                with photo_columns[1]:
                    st.caption(
                        f"**Photograph {position + 1} "
                        f"of {len(ordered_photos)}**"
                    )

                    st.caption(
                        uploaded_file.name
                    )

                    button_columns = st.columns(
                        3
                    )

                    with button_columns[0]:
                        if st.button(
                            "Rotate 90° left",
                            key=f"rotate-left-{photo_id}",
                            use_container_width=True,
                        ):
                            st.session_state.photo_rotations[
                                photo_id
                            ] = (
                                rotation_degrees + 90
                            ) % 360

                            st.rerun()

                    with button_columns[1]:
                        if st.button(
                            "Rotate 90° right",
                            key=f"rotate-right-{photo_id}",
                            use_container_width=True,
                        ):
                            st.session_state.photo_rotations[
                                photo_id
                            ] = (
                                rotation_degrees - 90
                            ) % 360

                            st.rerun()

                    with button_columns[2]:
                        if st.button(
                            "Reset rotation",
                            key=f"rotate-reset-{photo_id}",
                            use_container_width=True,
                        ):
                            st.session_state.photo_rotations[
                                photo_id
                            ] = 0

                            st.rerun()

                    order_columns = st.columns(
                        3
                    )

                    with order_columns[0]:
                        if st.button(
                            "Move up",
                            key=f"move-up-{photo_id}",
                            use_container_width=True,
                            disabled=(
                                position == 0
                            ),
                        ):
                            st.session_state.photo_keys[
                                position
                            ], st.session_state.photo_keys[
                                position - 1
                            ] = (
                                st.session_state.photo_keys[
                                    position - 1
                                ],
                                st.session_state.photo_keys[
                                    position
                                ],
                            )

                            st.rerun()

                    with order_columns[1]:
                        if st.button(
                            "Move down",
                            key=f"move-down-{photo_id}",
                            use_container_width=True,
                            disabled=(
                                position
                                == len(
                                    ordered_photos
                                )
                                - 1
                            ),
                        ):
                            st.session_state.photo_keys[
                                position
                            ], st.session_state.photo_keys[
                                position + 1
                            ] = (
                                st.session_state.photo_keys[
                                    position + 1
                                ],
                                st.session_state.photo_keys[
                                    position
                                ],
                            )

                            st.rerun()

                    with order_columns[2]:
                        if st.button(
                            "Remove",
                            key=f"remove-{photo_id}",
                            use_container_width=True,
                        ):
                            st.session_state.removed_photo_ids.add(
                                photo_id
                            )

                            if (
                                photo_id
                                in st.session_state.photo_keys
                            ):
                                st.session_state.photo_keys.remove(
                                    photo_id
                                )

                            st.rerun()

                    st.text_input(
                        "Caption",
                        key=f"caption-{photo_id}",
                        placeholder=(
                            "For example: John with Mum and Sarah"
                        ),
                    )

    else:
        st.info(
            "No photographs have been selected yet."
        )

    st.divider()

    st.subheader("When was it taken?")

    st.markdown(
        """
        Please enter the year in four-digit format, for example 1978.

        If you are not sure of the exact year, choose a decade below and then
        select the closest year.
        """
    )

    exact_year = st.text_input(
        "Year of photograph (YYYY)",
        key="photo_year",
        placeholder="For example: 1978",
    )

    selected_decade = st.selectbox(
        "Not sure? Choose a decade",
        options=[""] + DECADES,
        key="year_decade",
    )

    chosen_year = ""

    if selected_decade:
        decade_start = int(
            selected_decade[:4]
        )

        decade_years = [
            str(year)
            for year in range(
                decade_start,
                decade_start + 10,
            )
        ]

        chosen_year = st.selectbox(
            "Choose a year within this decade",
            options=[""] + decade_years,
            key="year_from_decade",
        )

    st.info(
        "Your draft is saved automatically while this page remains open."
    )

    st.markdown("")

    button_columns = st.columns(2)

    with button_columns[0]:
        if st.button(
            "← Back",
            use_container_width=True,
        ):
            st.session_state.submission_step = 1
            st.rerun()

    with button_columns[1]:
        if st.button(
            "Check and share →",
            use_container_width=True,
        ):
            if not uploaded_files:
                st.error(
                    "Please choose at least one photograph."
                )
                return

            if len(uploaded_files) > MAX_PHOTOS:
                st.error(
                    f"Please upload no more than {MAX_PHOTOS} photographs."
                )
                return

            final_year = (
                exact_year.strip()
                or chosen_year
            )

            if not re.fullmatch(
                r"\d{4}",
                final_year,
            ):
                st.error(
                    "Please enter a four-digit year, for example 1978."
                )
                return

            st.session_state.submission_step = 3
            st.rerun()


def render_step_three(
    uploaded_files,
):
    st.subheader("Check your photographs")

    st.markdown(
        f"""
        **Shared by:** {st.session_state.submitter_name}

        **Year:** {st.session_state.photo_year or "Not provided"}

        **Photographs:** {len(uploaded_files)}
        """
    )

    if not uploaded_files:
        st.warning(
            "No photographs have been selected."
        )

    ordered_photos = sync_photo_state(
        uploaded_files
    )

    for position, (
        photo_id,
        uploaded_file,
    ) in enumerate(
        ordered_photos
    ):
        rotation_degrees = (
            st.session_state.photo_rotations.get(
                photo_id,
                0,
            )
        )

        with st.container(border=True):
            preview_columns = st.columns(
                [1, 2]
            )

            with preview_columns[0]:
                try:
                    rotated_preview = (
                        get_rotated_preview(
                            uploaded_file,
                            rotation_degrees,
                        )
                    )

                    st.image(
                        rotated_preview,
                        use_container_width=True,
                    )
                except Exception:
                    st.caption(
                        uploaded_file.name
                    )

            with preview_columns[1]:
                st.caption(
                    f"**Photograph {position + 1} "
                    f"of {len(ordered_photos)}**"
                )

                caption = st.session_state.get(
                    f"caption-{photo_id}",
                    "",
                )

                st.markdown(
                    f"**Caption:** {caption or 'No caption provided'}"
                )

    st.info(
        "Your photographs will be reviewed by the family before appearing "
        "in the shared gallery."
    )

    st.markdown("")

    button_columns = st.columns(2)

    with button_columns[0]:
        if st.button(
            "← Back",
            use_container_width=True,
        ):
            st.session_state.submission_step = 2
            st.rerun()

    with button_columns[1]:
        if st.button(
            "Share these photographs",
            use_container_width=True,
        ):
            submit_photos(
                uploaded_files,
            )


def submit_photos(
    uploaded_files,
):
    errors = []

    if not st.session_state.submitter_name.strip():
        errors.append(
            "Please enter your name."
        )

    if not uploaded_files:
        errors.append(
            "Please choose at least one photograph."
        )

    if uploaded_files and len(uploaded_files) > MAX_PHOTOS:
        errors.append(
            f"Please upload no more than {MAX_PHOTOS} photographs."
        )

    if not re.fullmatch(
        r"\d{4}",
        st.session_state.photo_year.strip(),
    ):
        errors.append(
            "Please enter a four-digit year, for example 1978."
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
        "submitter_name": st.session_state.submitter_name.strip(),
        "photo_year": int(
            st.session_state.photo_year.strip()
        ),
        "status": "pending",
    }

    uploaded_photo_records = []

    ordered_photos = sync_photo_state(
        uploaded_files
    )

    try:
        progress_bar = st.progress(
            0,
            text="Preparing your photographs...",
        )

        for position, (
            photo_id,
            uploaded_file,
        ) in enumerate(
            ordered_photos,
            start=1,
        ):
            progress_bar.progress(
                position / len(ordered_photos),
                text=(
                    f"Uploading photograph {position} "
                    f"of {len(ordered_photos)}..."
                ),
            )

            rotation_degrees = (
                st.session_state.photo_rotations.get(
                    photo_id,
                    0,
                )
            )

            (
                file_bytes,
                content_type,
                original_filename,
            ) = prepare_image_for_upload(
                uploaded_file,
                rotation_degrees,
            )

            filename = safe_filename(
                original_filename
            )

            storage_path = (
                f"pending/{submission_id}/"
                f"{position}-{filename}"
            )

            public_client.storage.from_(
                bucket
            ).upload(
                file=file_bytes,
                path=storage_path,
                file_options={
                    "content-type": content_type,
                    "cache-control": "3600",
                    "upsert": "false",
                },
            )

            caption = st.session_state.get(
                f"caption-{photo_id}",
                "",
            )

            uploaded_photo_records.append(
                {
                    "submission_id": submission_id,
                    "storage_path": storage_path,
                    "original_filename": original_filename,
                    "photo_order": position,
                    "caption": caption.strip() or None,
                }
            )

        progress_bar.progress(
            1.0,
            text="Saving your photographs...",
        )

        public_client.table(
            "submissions"
        ).insert(
            submission_record,
            returning="minimal",
        ).execute()

        public_client.table(
            "submission_photos"
        ).insert(
            uploaded_photo_records,
            returning="minimal",
        ).execute()

        progress_bar.empty()

        if st.session_state.submitter_email.strip():
            send_confirmation_email(
                st.session_state.submitter_email.strip(),
                st.session_state.submitter_name.strip(),
            )

        st.session_state.submission_complete = True
        st.rerun()

    except Exception as error:
        progress_bar.empty()

        st.error(
            "Sorry, we could not save your photographs. "
            "Please try again, or email the photographs to the family."
        )

        error_details = getattr(
            error,
            "args",
            None,
        )

        if error_details:
            st.error(
                f"Technical details: {error_details}"
            )
        else:
            st.error(
                f"Technical details: {error}"
            )


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

    initialise_submission_state()

    if st.session_state.submission_complete:
        st.success(
            "Thank you — your photographs have been received."
        )

        st.markdown(
            """
            Your photographs and captions will help us create a collection of
            memories to share at John’s wake, and to keep for the family
            afterwards.

            Your photographs will be reviewed by the family before appearing
            in the shared gallery.
            """
        )

        if st.button(
            "Share more photographs",
            use_container_width=True,
        ):
            st.session_state.submission_complete = False
            st.session_state.submission_step = 1
            st.rerun()

        return

    uploaded_files = st.session_state.get(
        "photo_uploader"
    )

    if uploaded_files is None:
        uploaded_files = []

    render_step_indicator(
        st.session_state.submission_step
    )

    st.markdown("")

    if st.session_state.submission_step == 1:
        render_step_one()

    elif st.session_state.submission_step == 2:
        render_step_two(
            uploaded_files,
        )

    elif st.session_state.submission_step == 3:
        render_step_three(
            uploaded_files,
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
                    st.markdown(
                        f"**Year:** {submission.get('photo_year') or 'Not provided'}"
                    )
                    st.markdown(
                        f"**Photographs:** {photo_count}"
                    )


def render_gallery(service_client, submissions):
    st.subheader("Photo gallery")

    st.markdown(
        """
        Photographs are shown in year order, from the earliest year to the
        most recent. Select a memory to see all of its photographs and
        captions.
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
        key=lambda submission: submission.get(
            "photo_year"
        )
        or 9999,
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

            st.caption(
                f"**{submission.get('photo_year') or 'Year unknown'}**"
            )

            st.caption(
                f"Shared by "
                f"{submission.get('submitter_name') or 'Unknown'}"
            )

            if st.button(
                "View photographs and captions",
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
        f"{submission.get('photo_year') or 'Year unknown'}"
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

    for photo in photos:
        with st.container(border=True):
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
                f"**Photograph {photo['photo_order']} "
                f"of {len(photos)}**"
            )

            if photo.get("caption"):
                st.markdown(
                    f"**Caption:** {photo['caption']}"
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
                    f"**Year:** "
                    f"{submission.get('photo_year') or 'Not provided'}"
                )
                st.markdown(
                    f"**Current status:** `{submission['status']}`"
                )

                photos = submission.get(
                    "photos",
                    [],
                )

                if photos:
                    for photo in photos:
                        with st.container(border=True):
                            preview_columns = st.columns(
                                [1, 2]
                            )

                            with preview_columns[0]:
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

                            with preview_columns[1]:
                                st.caption(
                                    f"**Photograph {photo['photo_order']}**"
                                )
                                st.caption(
                                    photo[
                                        "original_filename"
                                    ]
                                )

                                st.text_input(
                                    "Caption",
                                    value=photo.get(
                                        "caption"
                                    )
                                    or "",
                                    key=(
                                        f"curator-caption-"
                                        f"{photo['id']}"
                                    ),
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

                if st.button(
                    "Save changes",
                    key=f"save-{submission['id']}",
                ):
                    service_client.table(
                        "submissions"
                    ).update(
                        {
                            "status": new_status,
                        }
                    ).eq(
                        "id",
                        submission["id"],
                    ).execute()

                    for photo in photos:
                        caption_key = (
                            f"curator-caption-"
                            f"{photo['id']}"
                        )

                        updated_caption = (
                            st.session_state.get(
                                caption_key,
                                "",
                            )
                        )

                        service_client.table(
                            "submission_photos"
                        ).update(
                            {
                                "caption": (
                                    updated_caption.strip()
                                    or None
                                ),
                            }
                        ).eq(
                            "id",
                            photo["id"],
                        ).execute()

                    st.success("Saved.")
                    st.rerun()

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
                        "photo_year": submission.get(
                            "photo_year"
                        )
                        or "",
                        "photo_filename": "",
                        "photo_storage_path": "",
                        "photo_order": "",
                        "caption": "",
                        "submission_status": submission[
                            "status"
                        ],
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
                        "photo_year": submission.get(
                            "photo_year"
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
                        "caption": photo.get(
                            "caption"
                        )
                        or "",
                        "submission_status": submission[
                            "status"
                        ],
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

            Each row represents one photograph, including its caption and the
            year entered for the submission.
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
