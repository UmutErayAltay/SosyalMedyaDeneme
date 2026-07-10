// Tarayıcı confirm() yerine özel modal
window.appConfirm = (function() {
  let modal = null;
  let resolveCallback = null;

  function createModal() {
    const overlay = document.createElement('div');
    overlay.className = 'app-confirm-overlay';
    overlay.setAttribute('role', 'presentation');

    const box = document.createElement('div');
    box.className = 'app-confirm-box';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');

    const message = document.createElement('p');
    message.className = 'app-confirm-message';
    box.appendChild(message);

    const buttons = document.createElement('div');
    buttons.className = 'app-confirm-buttons';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = 'Vazgeç';
    cancelBtn.addEventListener('click', () => close(false));

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn btn-danger';
    confirmBtn.textContent = 'Evet';
    confirmBtn.addEventListener('click', () => close(true));

    buttons.appendChild(cancelBtn);
    buttons.appendChild(confirmBtn);
    box.appendChild(buttons);
    overlay.appendChild(box);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close(false);
    });

    document.body.appendChild(overlay);
    return { overlay, box, message, cancelBtn, confirmBtn };
  }

  function show(message) {
    return new Promise((resolve) => {
      if (!modal) {
        modal = createModal();
      }

      resolveCallback = resolve;
      modal.message.textContent = message;
      modal.overlay.removeAttribute('hidden');
      modal.cancelBtn.focus();

      const handleEscape = (e) => {
        if (e.key === 'Escape') {
          document.removeEventListener('keydown', handleEscape);
          close(false);
        }
      };
      document.addEventListener('keydown', handleEscape);
    });
  }

  function close(result) {
    if (modal) {
      modal.overlay.setAttribute('hidden', '');
      if (resolveCallback) {
        resolveCallback(result);
        resolveCallback = null;
      }
    }
  }

  return show;
})();

// Tarayıcı alert() yerine özel modal (tek "Tamam" butonu) — appConfirm ile
// aynı görsel dil. call.js arama uyarıları ("Cevap yok", "Arama reddedildi"
// vb.) başta olmak üzere sitedeki tüm bilgi kutuları bunu kullanır
// (kullanıcı isteği: tarayıcının kendi bildirim kutusu çıkmasın).
window.appAlert = (function () {
  let modal = null;
  let resolveCallback = null;

  function createModal() {
    const overlay = document.createElement('div');
    overlay.className = 'app-confirm-overlay';
    overlay.setAttribute('role', 'presentation');

    const box = document.createElement('div');
    box.className = 'app-confirm-box';
    box.setAttribute('role', 'alertdialog');
    box.setAttribute('aria-modal', 'true');

    const message = document.createElement('p');
    message.className = 'app-confirm-message';
    box.appendChild(message);

    const buttons = document.createElement('div');
    buttons.className = 'app-confirm-buttons';

    const okBtn = document.createElement('button');
    okBtn.type = 'button';
    okBtn.className = 'btn btn-primary';
    okBtn.textContent = 'Tamam';
    okBtn.addEventListener('click', close);

    buttons.appendChild(okBtn);
    box.appendChild(buttons);
    overlay.appendChild(box);

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) close();
    });

    document.body.appendChild(overlay);
    return { overlay, box, message, okBtn };
  }

  function show(message) {
    return new Promise((resolve) => {
      if (!modal) {
        modal = createModal();
      }

      resolveCallback = resolve;
      modal.message.textContent = message;
      modal.overlay.removeAttribute('hidden');
      modal.okBtn.focus();

      const handleEscape = (e) => {
        if (e.key === 'Escape') {
          document.removeEventListener('keydown', handleEscape);
          close();
        }
      };
      document.addEventListener('keydown', handleEscape);
    });
  }

  function close() {
    if (modal) {
      modal.overlay.setAttribute('hidden', '');
      if (resolveCallback) {
        resolveCallback();
        resolveCallback = null;
      }
    }
  }

  return show;
})();

// Form entegrasyonu: data-confirm attribute'ü olan formlar submit olurken onay ister
document.addEventListener('submit', async (e) => {
  const form = e.target;
  if (form.dataset.confirm) {
    e.preventDefault();

    const confirmed = await window.appConfirm(form.dataset.confirm);
    if (confirmed) {
      // form.submit() yeniden submit event'i tetikleyeceği için,
      // geçici olarak data-confirm'i kaldırıp tekrar submit et
      form.removeAttribute('data-confirm');
      form.submit();
    }
  }
}, true); // Capture phase da yakala, böylece diğer handler'lar çalışmadan önce engelle
