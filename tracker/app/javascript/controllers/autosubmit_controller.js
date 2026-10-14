import { Controller } from "@hotwired/stimulus"

// Submit the filter form as you type, into the results Turbo Frame.
//
// Two things make this feel right rather than frantic:
//
//   A debounce on text input. Submitting on every keystroke would fire a query
//   per character against a 70,000-row table with no indexes. 300ms is the pause
//   that reads as "I have finished typing".
//
//   Immediate submit for selects and checkboxes. Those are single, deliberate
//   actions - waiting 300ms after a dropdown change just feels broken.
//
// The form targets a Turbo Frame, so only the result list is replaced and the
// input keeps focus and cursor position. That is the whole reason this is a frame
// and not a full page load.
export default class extends Controller {
  static values = { delay: { type: Number, default: 300 } }

  disconnect() {
    clearTimeout(this.timeout)
  }

  schedule() {
    clearTimeout(this.timeout)
    this.timeout = setTimeout(() => this.submit(), this.delayValue)
  }

  submit() {
    clearTimeout(this.timeout)
    this.element.requestSubmit()
  }
}
