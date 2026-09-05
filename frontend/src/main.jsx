import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'

import Chat from './pages/Chat.jsx'
import AdminLogin from './pages/AdminLogin.jsx'
import AdminPanel from './pages/AdminPanel.jsx'
import './styles.css'

// Three routes, three top-level pages — no nested layouts needed since Chat
// and Admin share almost no UI chrome.
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Chat />} />
        <Route path="/admin" element={<AdminLogin />} />
        <Route path="/admin/panel" element={<AdminPanel />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
)
