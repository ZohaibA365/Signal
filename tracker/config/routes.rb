Rails.application.routes.draw do
  resource :session

  resources :postings, only: :index

  # Password reset was removed with PasswordsController: it needs Action Mailer to
  # deliver a token, and Action Mailer is deliberately not installed (this app
  # never sends anything). For one user, a reset is `bin/rails db:seed`.

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  # Can be used by load balancers and uptime monitors to verify that the app is live.
  get "up" => "rails/health#show", as: :rails_health_check

  # Render dynamic PWA files from app/views/pwa/* (remember to link manifest in application.html.erb)
  # get "manifest" => "rails/pwa#manifest", as: :pwa_manifest
  # get "service-worker" => "rails/pwa#service_worker", as: :pwa_service_worker

  root "postings#index"
end
