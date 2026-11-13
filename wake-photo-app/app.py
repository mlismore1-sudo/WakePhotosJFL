def render_gallery(service_client, submissions):
    st.subheader("Photo gallery")

    st.markdown(
        """
        Photographs are shown in time order, from the earliest date to the
        most recent. Select a photograph to see the full set of images and
        the memory behind it.
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
                signed = (
                    service_client.storage.from_(
                        st.secrets[
                            "SUPABASE_BUCKET"
                        ]
                    ).create_signed_url(
                        first_photo[
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
                f"**{date_text}**\n\n"
                f"{submission.get('people_in_photo') or ''}"
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

    st.markdown("")

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

    st.subheader(
        date_text
    )

    st.markdown(
        f"**People / occasion:** "
        f"{submission.get('people_in_photo') or 'Not provided'}"
    )

    if submission.get(
        "location"
    ):
        st.markdown(
            f"**Location:** {submission['location']}"
        )

    if submission.get(
        "postcode"
    ):
        st.markdown(
            f"**Postcode / ZIP:** "
            f"{submission['postcode']}"
        )

    if submission.get(
        "memory"
    ):
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

    for index, photo in enumerate(
        photos
    ):
        with photo_columns[
            index % 2
        ]:
            try:
                signed = (
                    service_client.storage.from_(
                        st.secrets[
                            "SUPABASE_BUCKET"
                        ]
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
