'use strict';

// Pure structural adaptation from Controller.forwardingShape. This deliberately
// does not depend on virtual observation order: Python reads the virtual source.
function forwardingShape(store, room, id, value) {
  let url;
  try { url = new URL(value); } catch { return false; }
  if (!room.zoneId || !room.rendererIds.includes(id) || url.protocol !== 'http:' ||
      url.hostname !== store.host || +url.port < 49152 || +url.port > 65535 ||
      url.username || url.password || url.hash) return false;
  const normalize = id => id.toLowerCase().replace(/^(?:urn:uuid:|uuid:)/, '');
  let parts;
  try { parts = url.pathname.slice(1).split('/').map(decodeURIComponent); } catch { return false; }
  const ids = [room.zoneId, room.id, id].map(normalize);
  if (new Set(ids).size !== 3 || parts.length !== 4 ||
      !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(parts[3]) ||
      !ids.every((id, index) => parts[index] && normalize(parts[index]) === id)) return false;
  const query = [...url.searchParams.entries()];
  return query.length === 1 && query[0][0] === 'punch' && /^\d{1,20}$/.test(query[0][1]);
}
module.exports = {forwardingShape};
