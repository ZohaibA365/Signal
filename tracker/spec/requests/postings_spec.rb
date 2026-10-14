require "rails_helper"

RSpec.describe "Browsing postings" do
  before(:all) do
    Warehouse.reset!
    @intern = Warehouse.posting(company_name: "Capital One", job_title: "Data Scientist Intern",
                                seniority: "intern", fit_score: 88, recommendation: "apply now")
    @senior = Warehouse.posting(company_name: "Shopify", job_title: "Senior Data Engineer",
                                seniority: "senior", fit_score: 60, recommendation: "worth a look")
  end

  after(:all) { Warehouse.reset! }

  before { sign_in }

  it "lists the scored postings" do
    get postings_path
    expect(response).to have_http_status(:ok)
    expect(response.body).to include("Capital One", "Shopify")
  end

  it "applies a filter from the query string" do
    get postings_path, params: { seniority: "intern" }
    expect(response.body).to include("Capital One")
    expect(response.body).not_to include("Senior Data Engineer")
  end

  it "searches across company and title" do
    get postings_path, params: { q: "shopify senior" }
    expect(response.body).to include("Shopify")
    expect(response.body).not_to include("Data Scientist Intern")
  end

  # The results list is a Turbo Frame so a keystroke replaces the rows and leaves
  # the filter input focused. Without the frame the whole page would reload.
  it "wraps the results in a turbo frame" do
    get postings_path
    expect(response.body).to include('<turbo-frame id="results"')
  end

  it "survives hostile parameters" do
    get postings_path, params: { country: "zz", sort: "; DROP TABLE apply_queue", min_fit: "abc" }
    expect(response).to have_http_status(:ok)
    expect(Posting.count).to eq(2)
  end
end
