'use strict';

function evaluate(snapshot, request) {
  const room = snapshot.rooms.find(r => r.id === request.roomId);
  const decision = (policyAllowed, reason) => ({apiVersion: '1',
    roomId: request.roomId, revision: snapshot.revision, policyAllowed,
    executable: false, reason, executionBlock: 'playback_not_implemented'});
  if (!room) return decision(false, 'room_not_found');
  if (!snapshot.topologyFresh || !room.fresh) return decision(false, 'state_unavailable');
  // This endpoint evaluates policy only; it never creates an ownership lease.
  // An explicit selection is scoped to this room and snapshot, never persisted.
  if (request.intent === 'explicit_takeover') return decision(true, 'explicit_selection');
  if (room.source === 'spotify') return decision(false, 'spotify_protected');
  if (room.source === 'unknown') return decision(false, 'unknown_source');
  return decision(false, 'external_source');
}

module.exports = {evaluate};
