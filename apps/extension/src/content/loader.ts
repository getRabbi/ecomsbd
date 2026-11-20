// A manifest content script cannot be an ES module; this classic script loads
// the real one (content/main.js) from the extension's own files.
void import(chrome.runtime.getURL('content/main.js'));
