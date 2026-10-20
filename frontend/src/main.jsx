
import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Bell,
  Check,
  ChevronDown,
  ClipboardList,
  FileText,
  Home,
  Search,
  ShieldCheck,
  Settings,
  Upload,
  Camera,
  X,
  MoreVertical,
  Clock3,
  Trash2,
  ExternalLink,
  Plus,
  Mail,
  RefreshCw,
  LogOut
} from "lucide-react";
import "./styles.css";

const API = (
  import.meta.env.VITE_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

const FILTERS = [
  "All Documents",
  "Active Warranties",
  "Expiring Soon",
  "No Warranty"
];

async function apiFetch(path, options = {}) {
  return fetch(`${API}${path}`, {
    ...options,
    credentials: "include"
  });
}

function formatDate(value) {
  if (!value) return "—";

  const d = new Date(`${value}T00:00:00`);

  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric"
      });
}

function money(value) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }

  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0
  }).format(value);
}

function Status({ status }) {
  const cls =
    status === "Active"
      ? "active"
      : status === "Expiring Soon"
        ? "soon"
        : status === "Expired"
          ? "expired"
          : "none";

  return (
    <span className={`status ${cls}`}>
      {status || "No Warranty"}
    </span>
  );
}

function App() {
  const [bills, setBills] = useState([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All Documents");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [selectedBill, setSelectedBill] = useState(null);
  const [notice, setNotice] = useState("");
  const [activeNav, setActiveNav] = useState("Home");

  const [gmail, setGmail] = useState({
    checking: true,
    signedIn: false,
    email: ""
  });

  const [scanning, setScanning] = useState(false);
  const [gmailError, setGmailError] = useState("");

  // ------------------------------------------------
  // Load bills
  // ------------------------------------------------

  async function loadBills() {
    setLoading(true);

    try {
      const params = new URLSearchParams({
        q: query,
        status: filter
      });

      const res = await apiFetch(`/api/bills?${params}`);
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Could not load bills.");
      }

      setBills(data);
      setError("");
    } catch (e) {
      setError(
        `${e.message}. Check that the BillProof backend is running.`
      );
    } finally {
      setLoading(false);
    }
  }

  // ------------------------------------------------
  // Check Google sign-in
  // ------------------------------------------------

  async function checkGmail() {
    try {
      const res = await apiFetch("/api/auth/me");
      const data = await res.json();

      if (!res.ok) {
        throw new Error("Could not check Google sign-in.");
      }

      setGmail({
        checking: false,
        signedIn: Boolean(data.signed_in),
        email: data.email || ""
      });
    } catch {
      setGmail({
        checking: false,
        signedIn: false,
        email: ""
      });
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);

    if (params.get("gmail") === "connected") {
      setNotice("Google account connected successfully.");
      window.history.replaceState(
        {},
        "",
        window.location.pathname
      );
    }

    if (params.has("gmail_error")) {
      setGmailError(
        `Google sign-in did not complete: ${params.get("gmail_error")}`
      );

      window.history.replaceState(
        {},
        "",
        window.location.pathname
      );
    }

    checkGmail();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      loadBills();
    }, 180);

    return () => clearTimeout(timer);
  }, [query, filter]);

  // ------------------------------------------------
  // Gmail connection
  // ------------------------------------------------

  function connectGmail() {
    setGmailError("");

    // IMPORTANT:
    // Navigate directly to the backend OAuth endpoint.
    // Do not use fetch() here; the endpoint redirects to Google.
    window.location.href = `${API}/api/auth/google`;
  }

  async function scanGmail() {
    setScanning(true);
    setGmailError("");
    setNotice("");

    try {
      const res = await apiFetch("/api/gmail/scan", {
        method: "POST"
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Gmail scan failed.");
      }

      setNotice(
        `Scan complete: ${data.imported} bill(s) imported from ` +
        `${data.messages_checked} matching email(s).`
      );

      setFilter("All Documents");
      setQuery("");

      await loadBills();
    } catch (e) {
      setGmailError(e.message);
    } finally {
      setScanning(false);
    }
  }

  async function disconnectGmail() {
    const confirmed = window.confirm(
      "Sign out of this BillProof browser session?"
    );

    if (!confirmed) return;

    try {
      await apiFetch("/api/auth/logout", {
        method: "POST"
      });

      setGmail({
        checking: false,
        signedIn: false,
        email: ""
      });

      setNotice("You have signed out of BillProof.");
      await loadBills();
    } catch {
      setGmailError("Could not sign out. Please try again.");
    }
  }

  // ------------------------------------------------
  // Upload bill
  // ------------------------------------------------

  async function uploadFile(file, form) {
    const body = new FormData();

    body.append("file", file);
    body.append("warranty_months", form.warrantyMonths);
    body.append("category", form.category);
    body.append("product_override", form.product);
    body.append("seller_override", form.seller);
    body.append("purchase_date_override", form.purchaseDate);
    body.append("amount_override", form.amount);

    const res = await apiFetch("/api/bills", {
      method: "POST",
      body
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data.detail || "Upload failed.");
    }

    setNotice("Bill uploaded successfully.");
    setUploadOpen(false);
    setFilter("All Documents");
    setQuery("");

    await loadBills();
  }

  // ------------------------------------------------
  // Delete bill
  // ------------------------------------------------

  async function removeBill(bill) {
    const confirmed = window.confirm(
      `Delete "${bill.product}" and its stored file?`
    );

    if (!confirmed) return;

    try {
      const res = await apiFetch(`/api/bills/${bill.id}`, {
        method: "DELETE"
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Could not delete bill.");
      }

      setNotice("Bill deleted.");
      await loadBills();
    } catch (e) {
      setError(e.message);
    }
  }

  // ------------------------------------------------
  // Dashboard counts
  // ------------------------------------------------

  const counts = useMemo(
    () => ({
      all: bills.length,
      active: bills.filter(
        b => b.warranty_status === "Active"
      ).length,
      soon: bills.filter(
        b => b.warranty_status === "Expiring Soon"
      ).length
    }),
    [bills]
  );

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <ClipboardList size={24} />
          </span>
          <span>BillProof</span>
        </div>

        <nav className="side-nav">
          {[
            [Home, "Home"],
            [FileText, "My Documents"],
            [ShieldCheck, "Warranty Tracker"],
            [Bell, "Reminders"],
            [Settings, "Settings"]
          ].map(([Icon, label]) => (
            <button
              key={label}
              className={`nav-item ${
                activeNav === label ? "selected" : ""
              }`}
              onClick={() => {
                setActiveNav(label);

                if (label === "Warranty Tracker") {
                  setFilter("Active Warranties");
                } else if (
                  label === "My Documents" ||
                  label === "Home"
                ) {
                  setFilter("All Documents");
                } else if (label === "Reminders") {
                  setFilter("Expiring Soon");
                } else if (label === "Settings") {
                  setNotice(
                    "Account settings are being prepared."
                  );
                }
              }}
            >
              <Icon size={20} />
              <span>{label}</span>
            </button>
          ))}
        </nav>

        <div className="sidebar-note">
          <div className="paper-art">
            <FileText size={37} />
            <FileText size={32} />
          </div>

          <p>
            All your bills.
            <br />
            All your warranties.
            <br />
            Always with you.
          </p>

          <span className="scribble">〰</span>
        </div>

        <div className="sidebar-bottom">
          <ShieldCheck size={16} />
          Private by design
        </div>
      </aside>

      {/* Main content */}
      <main className="main-area">
        <header className="topbar">
          <div className="searchbox">
            <Search size={19} />

            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search bills, products or brands..."
            />

            {query && (
              <button onClick={() => setQuery("")}>
                <X size={16} />
              </button>
            )}
          </div>

          <div className="top-actions">
            <button
              className="icon-button"
              title="Notifications"
              onClick={() =>
                setNotice(
                  "Warranty reminders will appear here when scheduled."
                )
              }
            >
              <Bell size={20} />
              {counts.soon > 0 && <i />}
            </button>

            <div className="user-chip">
              <span className="avatar">U</span>
              <span>{gmail.email || "Hi, Urvi"}</span>
              <ChevronDown size={15} />
            </div>
          </div>
        </header>

        <section className="welcome">
          <div>
            <h1>
              Good evening, Urvi <span className="heart">♡</span>
            </h1>
            <p>
              Keep all your purchase bills and warranties in one place.
            </p>
          </div>

          <button
            className="primary-button"
            onClick={() => setUploadOpen(true)}
          >
            <Camera size={19} />
            Scan / Upload Bill
          </button>
        </section>

        {/* Gmail panel */}
        <section
          style={{
            background: "#ffffff",
            border: "1px solid #e3eaf2",
            borderRadius: 16,
            padding: 20,
            margin: "0 0 22px",
            boxShadow: "0 4px 18px rgba(30,55,90,0.04)"
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 16,
              flexWrap: "wrap"
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 13
              }}
            >
              <div
                style={{
                  width: 46,
                  height: 46,
                  borderRadius: 13,
                  background: "#edf4ff",
                  color: "#315fbd",
                  display: "grid",
                  placeItems: "center"
                }}
              >
                <Mail size={23} />
              </div>

              <div>
                <h2
                  style={{
                    margin: 0,
                    fontSize: 18,
                    color: "#172a46"
                  }}
                >
                  Find bills in Gmail
                </h2>

                <p
                  style={{
                    margin: "5px 0 0",
                    color: "#66758a",
                    fontSize: 13
                  }}
                >
                  {gmail.checking
                    ? "Checking connection…"
                    : gmail.signedIn
                      ? `Connected: ${gmail.email}`
                      : "Connect your email to discover invoice attachments."}
                </p>
              </div>
            </div>

            <div
              style={{
                display: "flex",
                gap: 9,
                flexWrap: "wrap"
              }}
            >
              {!gmail.signedIn && (
                <button
                  className="primary-button"
                  onClick={connectGmail}
                  disabled={gmail.checking}
                >
                  <Mail size={17} />
                  Connect Gmail
                </button>
              )}

              {gmail.signedIn && (
                <>
                  <button
                    className="primary-button"
                    onClick={scanGmail}
                    disabled={scanning}
                  >
                    <RefreshCw
                      size={17}
                      className={scanning ? "spin" : ""}
                    />

                    {scanning
                      ? "Scanning emails…"
                      : "Find email bills"}
                  </button>

                  <button
                    className="secondary-button"
                    onClick={disconnectGmail}
                  >
                    <LogOut size={16} />
                    Sign out
                  </button>
                </>
              )}
            </div>
          </div>

          <div
            style={{
              marginTop: 15,
              paddingTop: 13,
              borderTop: "1px solid #edf0f5",
              color: "#718096",
              fontSize: 12,
              lineHeight: 1.6
            }}
          >
            BillProof looks for invoice-related emails with PDF and image
            attachments. Extracted details may be incomplete, so review
            them before relying on warranty information.
          </div>

          {gmailError && (
            <div
              className="error-banner"
              style={{ marginTop: 12 }}
            >
              <span>{gmailError}</span>
              <button onClick={() => setGmailError("")}>
                Dismiss
              </button>
            </div>
          )}
        </section>

        {/* Statistics */}
        <section className="quick-stats">
          <div>
            <span className="stat-icon blue">
              <FileText size={18} />
            </span>
            <span>
              <b>{counts.all}</b>
              <small>Documents found</small>
            </span>
          </div>

          <div>
            <span className="stat-icon green">
              <ShieldCheck size={18} />
            </span>
            <span>
              <b>{counts.active}</b>
              <small>Active warranties</small>
            </span>
          </div>

          <div>
            <span className="stat-icon amber">
              <Clock3 size={18} />
            </span>
            <span>
              <b>{counts.soon}</b>
              <small>Expiring soon</small>
            </span>
          </div>
        </section>

        {/* Documents */}
        <section className="documents-section">
          <div className="section-head">
            <div className="filter-tabs">
              {FILTERS.map(f => (
                <button
                  key={f}
                  className={
                    filter === f ? "active-tab" : ""
                  }
                  onClick={() => setFilter(f)}
                >
                  {f}
                </button>
              ))}
            </div>

            <button
              className="sort-button"
              onClick={() =>
                setNotice("Newest bills are shown first.")
              }
            >
              Sort by <ChevronDown size={16} />
            </button>
          </div>

          {notice && (
            <div className="notice">
              <Check size={16} />
              <span>{notice}</span>
              <button onClick={() => setNotice("")}>
                <X size={15} />
              </button>
            </div>
          )}

          {error && (
            <div className="error-banner">
              <span>{error}</span>
              <button onClick={loadBills}>Retry</button>
            </div>
          )}

          <div className="bill-list">
            {loading ? (
              <div className="empty-state">
                Loading your bills…
              </div>
            ) : bills.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">
                  <FileText size={30} />
                </div>

                <h3>No bills here yet</h3>

                <p>
                  Upload your first invoice or connect Gmail to look
                  for invoice attachments.
                </p>

                <button
                  className="secondary-button"
                  onClick={() => setUploadOpen(true)}
                >
                  <Plus size={17} />
                  Add a bill
                </button>
              </div>
            ) : (
              bills.map(bill => (
                <article
                  className="bill-row"
                  key={bill.id}
                  onClick={() => setSelectedBill(bill)}
                >
                  <div className="receipt-thumb">
                    <FileText size={27} />
                    <span>INVOICE</span>
                  </div>

                  <div className="bill-main">
                    <h3>{bill.product}</h3>

                    <p>
                      {bill.seller || "Seller not detected"}
                      <i />
                      {formatDate(bill.purchase_date)}
                    </p>

                    <small>
                      {bill.category}
                      {bill.amount != null &&
                        ` · ${money(bill.amount)}`}
                    </small>
                  </div>

                  <div className="warranty-info">
                    <ShieldCheck
                      size={21}
                      className={
                        bill.warranty_end
                          ? "shield-green"
                          : "shield-muted"
                      }
                    />

                    <div>
                      <span>
                        {bill.warranty_months
                          ? `Warranty: ${bill.warranty_months} months`
                          : "No warranty details"}
                      </span>

                      <small>
                        {bill.warranty_end
                          ? `Expires ${formatDate(bill.warranty_end)}`
                          : "Add warranty duration to track expiry"}
                      </small>
                    </div>
                  </div>

                  <Status status={bill.warranty_status} />

                  <button
                    className="row-menu"
                    title="More actions"
                    onClick={e => {
                      e.stopPropagation();
                      setSelectedBill(bill);
                    }}
                  >
                    <MoreVertical size={19} />
                  </button>
                </article>
              ))
            )}
          </div>

          <div className="privacy-line">
            <ShieldCheck size={16} />
            Review extracted details before relying on warranty dates.
          </div>
        </section>

        <footer className="page-footer">
          BILLPROOF <span>•</span> TEAM PHASE SHIFT <span>•</span> VIPS-TC
        </footer>
      </main>

      {uploadOpen && (
        <UploadModal
          onClose={() => setUploadOpen(false)}
          onSubmit={uploadFile}
        />
      )}

      {selectedBill && (
        <BillModal
          bill={selectedBill}
          onClose={() => setSelectedBill(null)}
          onDelete={async bill => {
            await removeBill(bill);
            setSelectedBill(null);
          }}
          onUpdated={loadBills}
        />
      )}
    </div>
  );
}

// --------------------------------------------------
// Upload modal
// --------------------------------------------------

function UploadModal({ onClose, onSubmit }) {
  const [file, setFile] = useState(null);

  const [form, setForm] = useState({
    product: "",
    seller: "",
    purchaseDate: "",
    amount: "",
    warrantyMonths: "0",
    category: "Electronics"
  });

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  function update(key, value) {
    setForm(prev => ({ ...prev, [key]: value }));
  }

  async function submit(e) {
    e.preventDefault();

    if (!file) {
      setErr("Choose an invoice file first.");
      return;
    }

    setBusy(true);
    setErr("");

    try {
      await onSubmit(file, form);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <form className="modal" onSubmit={submit}>
        <div className="modal-head">
          <div>
            <span className="eyebrow">YOUR DOCUMENT VAULT</span>
            <h2>Scan / Upload Bill</h2>
            <p>
              Upload a receipt or invoice. You can correct the
              extracted details below.
            </p>
          </div>

          <button
            type="button"
            className="close-button"
            onClick={onClose}
          >
            <X />
          </button>
        </div>

        <label className="dropzone">
          <input
            type="file"
            accept=".jpg,.jpeg,.png,.webp,.pdf,image/*,application/pdf"
            onChange={e =>
              setFile(e.target.files?.[0] || null)
            }
          />

          <span className="upload-circle">
            <Upload size={23} />
          </span>

          <b>
            {file
              ? file.name
              : "Choose an invoice or receipt"}
          </b>

          <small>JPG, PNG, WEBP or PDF · up to 12 MB</small>
        </label>

        <div className="form-grid">
          <label>
            Product name
            <input
              value={form.product}
              onChange={e =>
                update("product", e.target.value)
              }
              placeholder="e.g. Dell Inspiron 15"
            />
          </label>

          <label>
            Seller / store
            <input
              value={form.seller}
              onChange={e =>
                update("seller", e.target.value)
              }
              placeholder="e.g. Amazon"
            />
          </label>

          <label>
            Purchase date
            <input
              type="date"
              value={form.purchaseDate}
              onChange={e =>
                update("purchaseDate", e.target.value)
              }
            />
          </label>

          <label>
            Amount (₹)
            <input
              type="number"
              min="0"
              value={form.amount}
              onChange={e =>
                update("amount", e.target.value)
              }
              placeholder="50000"
            />
          </label>

          <label>
            Warranty duration
            <select
              value={form.warrantyMonths}
              onChange={e =>
                update("warrantyMonths", e.target.value)
              }
            >
              <option value="0">Not known / no warranty</option>
              <option value="3">3 months</option>
              <option value="6">6 months</option>
              <option value="12">1 year</option>
              <option value="24">2 years</option>
              <option value="36">3 years</option>
              <option value="60">5 years</option>
            </select>
          </label>

          <label>
            Category
            <select
              value={form.category}
              onChange={e =>
                update("category", e.target.value)
              }
            >
              {[
                "Electronics",
                "Home & Kitchen",
                "Fashion",
                "Appliances",
                "Furniture",
                "Other"
              ].map(category => (
                <option key={category}>{category}</option>
              ))}
            </select>
          </label>
        </div>

        {err && <div className="inline-error">{err}</div>}

        <div className="modal-foot">
          <span>
            <ShieldCheck size={15} />
            Stored in your Cloudinary vault
          </span>

          <button
            className="primary-button"
            disabled={busy}
          >
            {busy ? "Processing…" : "Save bill"}
          </button>
        </div>
      </form>
    </div>
  );
}

// --------------------------------------------------
// Bill details modal
// --------------------------------------------------

function BillModal({
  bill,
  onClose,
  onDelete,
  onUpdated
}) {
  const [editing, setEditing] = useState(false);
  const [product, setProduct] = useState(bill.product || "");
  const [seller, setSeller] = useState(bill.seller || "");
  const [purchaseDate, setPurchaseDate] = useState(
    bill.purchase_date || ""
  );
  const [amount, setAmount] = useState(bill.amount ?? "");
  const [months, setMonths] = useState(
    String(bill.warranty_months || 0)
  );
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);

    try {
      const res = await apiFetch(`/api/bills/${bill.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          product,
          seller,
          purchase_date: purchaseDate,
          amount: amount === "" ? null : Number(amount),
          warranty_months: Number(months)
        })
      });

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Save failed.");
      }

      await onUpdated();
      setEditing(false);
      onClose();
    } catch (e) {
      window.alert(e.message || "Could not save changes.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="modal-backdrop"
      onMouseDown={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal detail-modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">BILL DETAILS</span>
            <h2>{bill.product}</h2>
            <p>
              {bill.seller || "Seller not detected"}
              {" · "}
              {formatDate(bill.purchase_date)}
            </p>
          </div>

          <button
            className="close-button"
            onClick={onClose}
          >
            <X />
          </button>
        </div>

        <div className="detail-status">
          <Status status={bill.warranty_status} />

          <span>
            {bill.warranty_end
              ? `Warranty expires ${formatDate(bill.warranty_end)}`
              : "Warranty end date not available"}
          </span>
        </div>

        {editing ? (
          <div className="form-grid">
            <label>
              Product
              <input
                value={product}
                onChange={e => setProduct(e.target.value)}
              />
            </label>

            <label>
              Seller
              <input
                value={seller}
                onChange={e => setSeller(e.target.value)}
              />
            </label>

            <label>
              Purchase date
              <input
                type="date"
                value={purchaseDate}
                onChange={e =>
                  setPurchaseDate(e.target.value)
                }
              />
            </label>

            <label>
              Amount
              <input
                type="number"
                value={amount}
                onChange={e => setAmount(e.target.value)}
              />
            </label>

            <label>
              Warranty duration
              <select
                value={months}
                onChange={e => setMonths(e.target.value)}
              >
                <option value="0">Unknown / none</option>
                <option value="3">3 months</option>
                <option value="6">6 months</option>
                <option value="12">1 year</option>
                <option value="24">2 years</option>
                <option value="36">3 years</option>
                <option value="60">5 years</option>
              </select>
            </label>
          </div>
        ) : (
          <div className="detail-grid">
            <div>
              <small>Purchase amount</small>
              <b>{money(bill.amount)}</b>
            </div>

            <div>
              <small>Warranty duration</small>
              <b>
                {bill.warranty_months
                  ? `${bill.warranty_months} months`
                  : "Not set"}
              </b>
            </div>

            <div>
              <small>Purchase date</small>
              <b>{formatDate(bill.purchase_date)}</b>
            </div>

            <div>
              <small>Warranty end</small>
              <b>{formatDate(bill.warranty_end)}</b>
            </div>
          </div>
        )}

        {bill.cloudinary_url && (
          <a
            className="file-link"
            href={bill.cloudinary_url}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={16} />
            Open original bill
            <ExternalLink size={13} />
          </a>
        )}

        <div className="modal-foot">
          <button
            className="danger-button"
            onClick={() => onDelete(bill)}
          >
            <Trash2 size={15} />
            Delete
          </button>

          <span className="modal-spacer" />

          {editing && (
            <button
              className="secondary-button"
              onClick={() => setEditing(false)}
            >
              Cancel
            </button>
          )}

          {editing ? (
            <button
              className="primary-button"
              disabled={saving}
              onClick={save}
            >
              {saving ? "Saving…" : "Save changes"}
            </button>
          ) : (
            <button
              className="primary-button"
              onClick={() => setEditing(true)}
            >
              Edit details
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);


