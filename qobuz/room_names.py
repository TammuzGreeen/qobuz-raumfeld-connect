"""Display-name compatibility without changing stable logical room identities."""


def room_display_name(room):
    name, room_id = room['name'], room['id']
    # Older setup appended the final six room-ID characters, not a release tag.
    # Match that exact generated suffix only; retain arbitrary custom names.
    suffix = ' Qobuz ' + room_id[-6:]
    if len(room_id) >= 6 and name.endswith(suffix):
        return name[:-len(suffix)] + ' Qobuz'
    return name
