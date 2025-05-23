import os
import re
import html as html_converter

def render_transcript(transcript_path, label_to_name, output_format="plain"):
    """
    Render a diarized transcript with mapped speaker names/roles.
    Args:
        transcript_path (str): Path to the diarized transcript file.
        label_to_name (dict): Mapping from diarization label to name/role.
        output_format (str): 'plain' for plaintext, 'html' for HTML output.
    Returns:
        str: Rendered transcript in the requested format.
    """
    if not os.path.exists(transcript_path):
        return "Transcript file not found."
    display_lines = []
    with open(transcript_path, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.match(r"(\[.*?\]\s*-\s*)(.+?)(\s*-\s*)(.*)", line)
            if m:
                prefix = m.group(1)
                diarization_label = m.group(2).strip()
                sep = m.group(3)
                text_content = m.group(4)
                speaker_name = label_to_name.get(diarization_label, label_to_name.get('Unknown', 'Unknown'))
                if output_format == "html":
                    escaped_text_content = html_converter.escape(text_content)
                    html_line = (f'<span style="color:#888;font-size:12px;">{prefix}</span> '
                                 f'<b style="color:#ff9800;">{speaker_name}</b>'
                                 f'<span style="color:#888;font-size:12px;">{sep}</span>'
                                 f'<span style="color:#eaeaea;">{escaped_text_content}</span>')
                    display_lines.append(html_line)
                else:
                    display_lines.append(f"{prefix}{speaker_name}{sep}{text_content.strip()}")
            else:
                if output_format == "html":
                    escaped_line = html_converter.escape(line.rstrip('\n'))
                    display_lines.append(f'<span style="color:#eaeaea;">{escaped_line}</span>')
                else:
                    display_lines.append(line.rstrip('\n'))
    if output_format == "html":
        return "<br>".join(display_lines)
    else:
        return "\n".join(display_lines) 