/** Join class names, dropping anything falsy. The only styling helper on the site. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
