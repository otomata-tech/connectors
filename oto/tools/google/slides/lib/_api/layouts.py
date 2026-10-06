"""Master layouts: resolution, slide creation, batch generation.

Extracted from `slides_client.py` (split by operation family, public surface
frozen): the bodies are unchanged. This mixin is never instantiated on its
own — it is composed into `SlidesClient`, which builds `slides_service` and
`drive_service`.
"""

from ..markup import parse_bold_markdown


class _LayoutsMixin:
    """Master layouts: resolution, slide creation, batch generation."""

    def get_layout_id_by_name(self, presentation_id, layout_name):
        """
        Get layout object ID by predefined layout name

        Args:
            presentation_id: ID of the presentation
            layout_name: Name like 'TITLE_AND_BODY', 'TITLE_ONLY', etc.

        Returns:
            str: Layout object ID, or None if not found
        """
        presentation = self.get_presentation(presentation_id)

        # Build a mapping of available layouts
        layouts_by_name = {}
        default_layout_id = None

        for layout in presentation.get('layouts', []):
            props = layout.get('layoutProperties', {})
            display_name = props.get('displayName', '')
            name = props.get('name', '')
            object_id = layout['objectId']

            # Map by API name (always available)
            layouts_by_name[name] = object_id

            # Also map by display name for easier lookup
            if display_name:
                layouts_by_name[display_name] = object_id

            # Remember DEFAULT or first layout as fallback
            if display_name == 'DEFAULT' or name == 'DEFAULT':
                default_layout_id = object_id
            elif default_layout_id is None:
                default_layout_id = object_id

        # Try to find the requested layout
        if layout_name in layouts_by_name:
            return layouts_by_name[layout_name]

        # Fallback: use DEFAULT for TITLE_AND_BODY if not available
        if layout_name == 'TITLE_AND_BODY' and 'DEFAULT' in layouts_by_name:
            return layouts_by_name['DEFAULT']

        # Last resort: return default
        return default_layout_id

    def add_slide(self, presentation_id, layout='BLANK', insertion_index=None):
        """
        Add a new slide to presentation

        Args:
            presentation_id: ID of the presentation
            layout: Layout type (BLANK, TITLE_ONLY, TITLE, SECTION_HEADER, etc.)
            insertion_index: Position to insert (None = end)

        Returns:
            str: Object ID of the created slide
        """
        # Try to find layout by name first (for templates)
        layout_id = self.get_layout_id_by_name(presentation_id, layout)

        if layout_id:
            # Use layout object ID
            requests = [{
                'createSlide': {
                    'slideLayoutReference': {
                        'layoutId': layout_id
                    }
                }
            }]
        else:
            # Use predefined layout name (for new presentations)
            requests = [{
                'createSlide': {
                    'slideLayoutReference': {
                        'predefinedLayout': layout
                    }
                }
            }]

        if insertion_index is not None:
            requests[0]['createSlide']['insertionIndex'] = insertion_index

        response = self.slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={'requests': requests}
        ).execute()

        return response['replies'][0]['createSlide']['objectId']

    def get_layouts_map(self, presentation_id):
        """
        Return {layout_name: {'objectId': ..., 'placeholders': [(type, index), ...]}}.

        Useful for discovering the layouts of an imported master before using
        them via `build_from_layouts`.
        """
        pres = self.get_presentation(presentation_id)
        out = {}
        for layout in pres.get('layouts', []):
            props = layout.get('layoutProperties', {})
            name = props.get('name', '')
            placeholders = []
            for el in layout.get('pageElements', []):
                if 'shape' in el and el['shape'].get('placeholder'):
                    ph = el['shape']['placeholder']
                    placeholders.append((ph.get('type'), ph.get('index', 0)))
            out[name] = {
                'objectId': layout['objectId'],
                'placeholders': placeholders,
                'displayName': props.get('displayName', ''),
            }
        return out

    def clear_all_slides(self, presentation_id, keepalive_layout_id=None):
        """
        Delete all existing slides while keeping a temporary slide
        (the Slides API refuses a presentation with 0 slides).

        Args:
            presentation_id: ID of the presentation
            keepalive_layout_id: layoutId of a temporary slide to insert at the end.
                If None, takes the first available layout.

        Returns:
            str: objectId of the temporary keepalive slide
                 (to delete after creating your real slides)
        """
        pres = self.slides_service.presentations().get(
            presentationId=presentation_id, fields='slides(objectId),layouts(objectId)'
        ).execute()
        existing = [s['objectId'] for s in pres.get('slides', [])]

        if keepalive_layout_id is None:
            layouts = pres.get('layouts', [])
            if not layouts:
                raise ValueError("No layout available for the keepalive")
            keepalive_layout_id = layouts[0]['objectId']

        keepalive_id = 'tmp_keepalive_oto'
        # If already present (re-run), reuse it
        if keepalive_id in existing:
            others = [s for s in existing if s != keepalive_id]
            if others:
                self.slides_service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={'requests': [{'deleteObject': {'objectId': s}} for s in others]},
                ).execute()
            return keepalive_id

        requests = [{
            'createSlide': {
                'objectId': keepalive_id,
                'slideLayoutReference': {'layoutId': keepalive_layout_id},
            }
        }]
        for s in existing:
            requests.append({'deleteObject': {'objectId': s}})
        self.slides_service.presentations().batchUpdate(
            presentationId=presentation_id, body={'requests': requests}
        ).execute()
        return keepalive_id

    def build_from_layouts(self, presentation_id, slides_def,
                           override_body_bold=True, parse_markdown_bold=True):
        """
        Generate a series of slides from a list of definitions, with few
        batchUpdates (= robust against the 60 writes/min/user quota).

        Approach:
        1. 1 `createSlide` batchUpdate with `placeholderIdMappings` → predictable
           objectIds.
        2. 1 `insertText` + `updateTextStyle` (bold) batchUpdate on all the
           placeholders to fill.

        Args:
            presentation_id: ID of the target presentation (already cleared if needed)
            slides_def: list of tuples `(slide_id, layout_id, fills)` where `fills`
                is a dict `{(placeholder_type, placeholder_index): text}`.
                `slide_id` must be at least 5 characters (API constraint).
            override_body_bold: if True (default), force `bold:False` on all
                BODY placeholders before applying the bold ranges from the
                markdown. Useful when the master renders BODY in bold by default
                (the Otomata template case) — otherwise `**bold**` is invisible.
            parse_markdown_bold: if True (default), parse the `**…**` segments
                of the text and apply `updateTextStyle bold:True` to them.

        Returns:
            list[str]: the list of objectIds of the created slides
        """
        # Phase 1 — create the slides + placeholderIdMappings
        create_reqs = []
        for idx, (slide_id, layout_id, fills) in enumerate(slides_def):
            if len(slide_id) < 5:
                raise ValueError(
                    f"slide_id {slide_id!r} must be at least 5 characters "
                    f"(Slides API constraint)"
                )
            ph_mappings = [
                {
                    'layoutPlaceholder': {'type': ph_type, 'index': ph_index},
                    'objectId': f'{slide_id}_{ph_type}_{ph_index}',
                }
                for (ph_type, ph_index) in fills.keys()
            ]
            create_reqs.append({
                'createSlide': {
                    'objectId': slide_id,
                    'insertionIndex': idx,
                    'slideLayoutReference': {'layoutId': layout_id},
                    'placeholderIdMappings': ph_mappings,
                }
            })

        if create_reqs:
            self.slides_service.presentations().batchUpdate(
                presentationId=presentation_id, body={'requests': create_reqs}
            ).execute()

        # Phase 2 — insertText + updateTextStyle
        fill_reqs = []
        for slide_id, _layout_id, fills in slides_def:
            for (ph_type, ph_index), text in fills.items():
                obj_id = f'{slide_id}_{ph_type}_{ph_index}'
                if parse_markdown_bold:
                    clean, bolds = parse_bold_markdown(text)
                else:
                    clean, bolds = text, []
                if not clean:
                    continue
                fill_reqs.append({
                    'insertText': {
                        'objectId': obj_id,
                        'text': clean,
                        'insertionIndex': 0,
                    }
                })
                # Break the master's bold inheritance (Otomata case) on BODY placeholders
                if override_body_bold and ph_type == 'BODY':
                    fill_reqs.append({
                        'updateTextStyle': {
                            'objectId': obj_id,
                            'textRange': {'type': 'ALL'},
                            'style': {'bold': False},
                            'fields': 'bold',
                        }
                    })
                # Apply the bold ranges from the markdown
                for start, end in bolds:
                    fill_reqs.append({
                        'updateTextStyle': {
                            'objectId': obj_id,
                            'textRange': {
                                'type': 'FIXED_RANGE',
                                'startIndex': start,
                                'endIndex': end,
                            },
                            'style': {'bold': True},
                            'fields': 'bold',
                        }
                    })

        if fill_reqs:
            self.slides_service.presentations().batchUpdate(
                presentationId=presentation_id, body={'requests': fill_reqs}
            ).execute()

        return [slide_id for slide_id, _, _ in slides_def]

