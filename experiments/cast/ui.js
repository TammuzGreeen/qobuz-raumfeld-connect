'use strict';
const byId = id => document.getElementById(id);
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST',
    headers: {Authorization: 'Bearer ' + byId('token').value, 'Content-Type':'application/json'},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'request_failed');
  return value;
}
async function refresh() {
  try {
    const state = await api('status');
    byId('room').replaceChildren(...state.rooms.map(room => {
      const option = document.createElement('option');
      option.value = room.id; option.textContent = room.name; return option;
    }));
    byId('room').value = state.selectedRoom;
    byId('group').checked = state.grouping === 'all_configured';
    byId('group').indeterminate = !['all_configured','individual'].includes(state.grouping);
    byId('grouping').textContent = 'Observed grouping: ' + state.grouping +
      '. Pending: ' + state.pending + '. Last failure: ' + (state.failure || 'none') +
      '. Playback confirmed stopped: ' + state.playbackStopped;
    byId('rooms').replaceChildren(...state.rooms.map(room => {
      const item = document.createElement('li');
      item.textContent = room.name + ': ' + (room.available ? 'available' : room.limitation) +
        (room.stopped ? ', stopped' : ', not confirmed stopped'); return item;
    }));
    byId('result').textContent = JSON.stringify(state.wire, null, 2);
  } catch (error) { byId('result').textContent = error.message; }
}
byId('refresh').onclick = refresh;
byId('select').onclick = async () => {
  try { await api('select', {roomId:byId('room').value}); await refresh(); }
  catch(error) { byId('result').textContent = error.message; }
};
byId('snapshot').onclick = async () => {
  try { byId('result').textContent = JSON.stringify(await api('snapshot', {})); }
  catch(error) { byId('result').textContent = error.message; }
};
