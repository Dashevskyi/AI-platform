export function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  // Fallback for HTTP
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.left = '-9999px';
  textarea.style.top = '-9999px';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  return new Promise((resolve, reject) => {
    // eslint-disable-next-line @typescript-eslint/no-deprecated
    document.execCommand('copy') ? resolve() : reject();
    document.body.removeChild(textarea);
  });
}
