'use strict';

// Convenience methods in the pinned node-raumkernel omit declared SOAP inputs.
// Keep dependency internals unchanged and use its supported callAction API.
function rendererCommand(device, method, args) {
  const commands = {
    setAvTransportUri: ['AVTransport', 'SetAVTransportURI', {InstanceID: 0, CurrentURI: args[0], CurrentURIMetaData: args[1]}],
    play: ['AVTransport', 'Play', {InstanceID: 0, Speed: '1'}],
    pause: ['AVTransport', 'Pause', {InstanceID: 0}],
    stop: ['AVTransport', 'Stop', {InstanceID: 0}],
    seek: ['AVTransport', 'Seek', {InstanceID: 0, Unit: args[0], Target: args[1]}],
    setVolume: ['RenderingControl', 'SetVolume', {InstanceID: 0, Channel: 'Master', DesiredVolume: args[0]}],
  };
  const command = commands[method];
  if (!command) throw new Error('Unsupported renderer command');
  return device.callAction(...command);
}

module.exports = {rendererCommand};
