// Links into Home Assistant's own pages.

import { html, type TemplateResult } from "lit";

/**
 * The companion apps show one page at a time: a new tab would leave the app
 * for the system browser, where the user is not signed in. There the device
 * page replaces this one, and the app's back gesture returns.
 */
const IN_APP = /Home ?Assistant\//.test(navigator.userAgent);

export const DEVICE_TARGET = IN_APP ? "_top" : "_blank";

/** Home Assistant's page of a device; same origin as ingress, so a path does. */
export function deviceHref(deviceId: string): string {
  return `/config/devices/device/${encodeURIComponent(deviceId)}`;
}

/** ``name`` as a link to the device's page, or plain where it has none. */
export function deviceName(
  name: string,
  deviceId: string | null | undefined,
): TemplateResult {
  return deviceId
    ? html`<a class="device" href=${deviceHref(deviceId)} target=${DEVICE_TARGET}
        rel="noopener">${name}</a>`
    : html`${name}`;
}
