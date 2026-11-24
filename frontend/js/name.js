// The app's name, as the server filled it into index.html (from backend/waudio/__init__.py, the
// one place it is written). Every sentence on the page that names the app takes it from here.
export const NAME = document.querySelector('meta[name="app-name"]').content;
