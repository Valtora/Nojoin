/** A rejected API request as the axios client delivers it to a catch block. */
export function httpError(status: number, detail?: string): Error {
  return Object.assign(
    new Error(`Request failed with status code ${status}`),
    { response: { status, data: detail === undefined ? {} : { detail } } },
  );
}
