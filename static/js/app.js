// Delete confirmation modal shared across pages
function confirmDelete(actionUrl, message) {
  const modal = document.getElementById('deleteModal');
  if (!modal) return;
  document.getElementById('deleteModalBody').innerHTML = message;
  document.getElementById('deleteForm').action = actionUrl;
  new bootstrap.Modal(modal).show();
}
